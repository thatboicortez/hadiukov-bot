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

# Mentoring (не показываем в кабинете, но оставим покупку если нужно)
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

# таймауты/ретраи под Koyeb (чтобы не отваливалось)
NOTION_TIMEOUT = 60
NOTION_RETRIES = 3


async def notion_query_database_raw(payload: dict) -> dict:
    """
    Низкоуровневый запрос в Notion /query с ретраями на таймаут/сетевые ошибки.
    """
    url = f"{NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    last_exc = None
    for attempt in range(1, NOTION_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=NOTION_TIMEOUT) as client:
                r = await client.post(url, headers=headers, json=payload)
                r.raise_for_status()
                return r.json()
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            last_exc = e
            # небольшая задержка + backoff
            await asyncio.sleep(0.6 * attempt)
        except httpx.HTTPStatusError as e:
            # 4xx/5xx - возвращаем наверх
            raise e

    raise RuntimeError(f"Notion request failed after retries: {last_exc}")


async def notion_query_database(filter_obj: dict | None, page_size: int = 20) -> dict:
    payload = {"page_size": page_size}
    if filter_obj:
        payload["filter"] = filter_obj

    # сортируем по created_time (самое новое сверху)
    payload["sorts"] = [{"timestamp": "created_time", "direction": "descending"}]

    return await notion_query_database_raw(payload)


def notion_get_prop_as_str(page: dict, prop_name: str) -> str:
    """
    Универсально достаём строку из:
    - rich_text
    - title
    - email
    - status
    - select
    - number
    - date
    """
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

    if t == "status":
        s = p.get("status") or {}
        return s.get("name", "") or ""

    if t == "select":
        s = p.get("select") or {}
        return s.get("name", "") or ""

    if t == "number":
        n = p.get("number")
        return "" if n is None else str(int(n) if float(n).is_integer() else n)

    if t == "date":
        d = p.get("date") or {}
        return d.get("start", "") or ""

    return ""


def parse_expires(expires_at_str: str) -> date | None:
    if not expires_at_str:
        return None
    # ожидаем "YYYY-MM-DD"
    try:
        return datetime.strptime(expires_at_str.strip(), "%Y-%m-%d").date()
    except Exception:
        return None


async def get_latest_record_for_user(tg_id: int) -> dict | None:
    """
    Берём самую свежую запись пользователя по tg_id.
    ВАЖНО: tg_id в Notion может быть Text ИЛИ Number — пробуем оба варианта.
    """
    tg_id_str = str(tg_id)

    # 1) пробуем как Text
    try:
        filter_obj = {"property": "tg_id", "rich_text": {"equals": tg_id_str}}
        data = await notion_query_database(filter_obj, page_size=50)
        results = data.get("results", [])
        if results:
            return results[0]
    except httpx.HTTPStatusError:
        pass

    # 2) пробуем как Number
    try:
        filter_obj = {"property": "tg_id", "number": {"equals": tg_id}}
        data = await notion_query_database(filter_obj, page_size=50)
        results = data.get("results", [])
        if results:
            return results[0]
    except httpx.HTTPStatusError:
        pass

    # 3) фолбек: без фильтра берём последние 100 и ищем вручную (если типы/фильтры капризные)
    data = await notion_query_database(filter_obj=None, page_size=100)
    results = data.get("results", [])
    for page in results:
        val = notion_get_prop_as_str(page, "tg_id")
        if val == tg_id_str:
            return page

    return None


async def get_active_approved_subscription(tg_id: int) -> dict | None:
    """
    Возвращает последнюю APPROVED запись пользователя, которая еще НЕ истекла.
    Поддерживает status как Status/Select/Text.
    """
    tg_id_str = str(tg_id)
    today = datetime.utcnow().date()

    # фолбек-метод (самый надежный): берём последние 100 и фильтруем на нашей стороне
    data = await notion_query_database(filter_obj=None, page_size=100)
    results = data.get("results", [])

    best = None
    best_created = None

    for page in results:
        if notion_get_prop_as_str(page, "tg_id") != tg_id_str:
            continue

        st = (notion_get_prop_as_str(page, "status") or "").strip().lower()
        if st != "approved":
            continue

        exp = notion_get_prop_as_str(page, "expires_at")
        exp_date = parse_expires(exp)
        if not exp_date or exp_date < today:
            continue

        created_time_str = page.get("created_time")  # ISO
        created_dt = None
        try:
            created_dt = datetime.fromisoformat(created_time_str.replace("Z", "+00:00"))
        except Exception:
            created_dt = None

        if best is None:
            best = page
            best_created = created_dt
        else:
            if created_dt and best_created and created_dt > best_created:
                best = page
                best_created = created_dt

    return best


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
      product, period
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

    # суммы (только одна заполняется)
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


# --- Личный кабинет ---
@dp.message(lambda m: "Личный кабинет" in (m.text or ""))
async def cabinet_from_menu(message: Message):
    try:
        # 1) сначала пытаемся найти активную approved-подписку (не истекла)
        active = await get_active_approved_subscription(message.from_user.id)
        if active:
            discord = notion_get_prop_as_str(active, "discord") or "Не указан"
            email = notion_get_prop_as_str(active, "email") or "Не указан"
            expires_at = notion_get_prop_as_str(active, "expires_at") or ""

            await message.answer(
                "👤 Личный кабинет\n\n"
                f"Discord: <b>{discord}</b>\n"
                f"Email: <b>{email}</b>\n\n"
                f"Подписка: <b>Hadiukov Community — активна до {expires_at}</b>"
            )
            return

        # 2) если нет активной approved — показываем статус последней заявки (pending/rejected/approved-но-истекла)
        last = await get_latest_record_for_user(message.from_user.id)
        if not last:
            await message.answer(
                "👤 Личный кабинет\n\n"
                "Discord: <b>Не указан</b>\n"
                "Email: <b>Не указан</b>\n\n"
                "Статус: <b>Нет активной подписки</b>"
            )
            return

        st = (notion_get_prop_as_str(last, "status") or "").strip().lower()
        discord = notion_get_prop_as_str(last, "discord") or "Не указан"
        email = notion_get_prop_as_str(last, "email") or "Не указан"
        expires_at = notion_get_prop_as_str(last, "expires_at") or ""

        if st == "pending":
            await message.answer(
                "👤 Личный кабинет\n\n"
                "Discord: <b>Не указан</b>\n"
                "Email: <b>Не указан</b>\n\n"
                "Статус: <b>Заявка проверяется</b>"
            )
            return

        if st == "rejected":
            await message.answer(
                "👤 Личный кабинет\n\n"
                "Discord: <b>Не указан</b>\n"
                "Email: <b>Не указан</b>\n\n"
                f"Статус: <b>Заявка отклонена</b>\n"
                f"Пожалуйста, свяжитесь с администратором: {ADMIN_USERNAME}"
            )
            return

        if st == "approved":
            # approved, но активной нет => значит скорее всего истекла
            if expires_at:
                await message.answer(
                    "👤 Личный кабинет\n\n"
                    f"Discord: <b>{discord}</b>\n"
                    f"Email: <b>{email}</b>\n\n"
                    f"Статус: <b>Подписка была активна до {expires_at}</b>"
                )
            else:
                await message.answer(
                    "👤 Личный кабинет\n\n"
                    f"Discord: <b>{discord}</b>\n"
                    f"Email: <b>{email}</b>\n\n"
                    "Статус: <b>Approved</b>"
                )
            return

        # неизвестный статус
        await message.answer(
            "👤 Личный кабинет\n\n"
            "Discord: <b>Не указан</b>\n"
            "Email: <b>Не указан</b>\n\n"
            "Статус: <b>Нет активной подписки</b>"
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
