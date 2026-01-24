import uuid
import asyncio
import logging
import random
import time
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
from aiogram.exceptions import TelegramNetworkError

from config import BOT_TOKEN, TALLY_FORM_URL, NOTION_TOKEN, NOTION_DATABASE_ID

# =========================
# LOGGING
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("bot")

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
USDT_TRC20_ADDRESS = "TX5VC5qAprsWcnCSSdgZGXtQMFD2JjVLyK"

# Prices
COMMUNITY_USDT_1M = 50
COMMUNITY_USDT_3M = 120
COMMUNITY_UAH_1M = 2200
COMMUNITY_UAH_3M = 5200

MENTORING_USDT = 3000
MENTORING_UAH = 130000

PERIOD_TEXT = {"1m": "1 month", "3m": "3 months"}
PERIOD_MONTHS = {"1m": 1, "3m": 3}

WELCOME_TEXT = (
    "Вас приветствует Sever by Hadiukov!\n\n"
    "Сейчас вы находитесь в официальном боте проекта.\n"
    "Здесь вы можете оформить или продлить подписку и отправить подтверждение оплаты.\n\n"
    "Выберите нужный раздел в меню снизу 👇\n"
    f"Если возникнут вопросы — напишите администратору {ADMIN_USERNAME}."
)

CABINET_RETRY_TEXT = "⏳ Подожди 10–20 секунд и нажми «Личный кабинет» ещё раз."

# =========================
# BOT INIT
# =========================

bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# =========================
# SAFE SEND (Telegram retry)
# =========================

async def safe_answer(message: Message, text: str, *, reply_markup=None, retries: int = 2):
    """
    Надёжная отправка сообщений: если Telegram временно не отвечает, пробуем ещё раз.
    Если совсем плохо — просто не валим процесс.
    """
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return await message.answer(text, reply_markup=reply_markup)
        except TelegramNetworkError as e:
            last_err = e
            log.warning(
                "TelegramNetworkError on answer (attempt %s/%s). chat_id=%s user_id=%s err=%s",
                attempt, retries, getattr(message.chat, "id", None),
                getattr(message.from_user, "id", None),
                repr(e),
            )
            await asyncio.sleep(1.0)
        except Exception as e:
            # Любая другая ошибка отправки
            log.exception(
                "Unexpected error on message.answer. chat_id=%s user_id=%s err=%s",
                getattr(message.chat, "id", None),
                getattr(message.from_user, "id", None),
                repr(e),
            )
            return None

    # не валим процесс, но логируем финальную ошибку
    if last_err:
        log.error(
            "Failed to send message after retries. chat_id=%s user_id=%s last_err=%s",
            getattr(message.chat, "id", None),
            getattr(message.from_user, "id", None),
            repr(last_err),
        )
    return None

# =========================
# NOTION (READ ONLY) + RETRIES/BACKOFF
# =========================

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# Таймауты и ретраи для Notion
NOTION_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=20.0)
NOTION_RETRIES = 4


async def notion_query_database(filter_obj: dict, page_size: int = 10) -> dict:
    """
    Query Notion DB с ретраями/backoff + логами времени.
    Ретраим:
      - timeout/transport errors
      - 429, 502, 503, 504
    """
    url = f"{NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    payload = {
        "filter": filter_obj,
        "page_size": page_size,
        "sorts": [{"timestamp": "created_time", "direction": "descending"}],
    }

    last_exc = None

    for attempt in range(1, NOTION_RETRIES + 1):
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=NOTION_TIMEOUT) as client:
                r = await client.post(url, headers=headers, json=payload)

            # временные статусы — ретраим
            if r.status_code in (429, 502, 503, 504):
                raise httpx.HTTPStatusError(
                    f"Notion temporary error: {r.status_code}",
                    request=r.request,
                    response=r,
                )

            r.raise_for_status()
            ms = int((time.perf_counter() - t0) * 1000)
            log.info("Notion query OK (%sms) attempt=%s/%s", ms, attempt, NOTION_RETRIES)
            return r.json()

        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as e:
            last_exc = e
            ms = int((time.perf_counter() - t0) * 1000)
            log.warning(
                "Notion query FAIL (%sms) attempt=%s/%s err=%s",
                ms, attempt, NOTION_RETRIES, repr(e),
            )

            if attempt < NOTION_RETRIES:
                # backoff: 1,2,4,... + jitter
                delay = (2 ** (attempt - 1)) + random.uniform(0.0, 0.35)
                log.info("Notion retrying after %.2fs", delay)
                await asyncio.sleep(delay)
            else:
                log.error("Notion query окончательно упал после ретраев: %s", repr(e))
                raise

        except Exception as e:
            # не ретраим неизвестные ошибки, но логируем
            log.exception("Notion query unexpected error: %s", repr(e))
            raise

    # теоретически недостижимо
    raise last_exc or RuntimeError("Notion query failed")


def _rt_plain(props: dict, prop_name: str) -> str:
    """
    Читает Notion Text (rich_text) как строку.
    """
    p = (props or {}).get(prop_name)
    if not p:
        return ""
    if p.get("type") != "rich_text":
        return ""
    arr = p.get("rich_text") or []
    if not arr:
        return ""
    return arr[0].get("plain_text", "") or ""


def _status_name(props: dict, prop_name: str = "status") -> str:
    """
    Читает Notion Status как name.
    Если вдруг сделаешь status обычным Text — тоже отработает (через rich_text).
    """
    p = (props or {}).get(prop_name)
    if not p:
        return ""
    t = p.get("type")
    if t == "status":
        s = p.get("status") or {}
        return (s.get("name") or "").strip().lower()
    if t == "rich_text":
        return (_rt_plain(props, prop_name) or "").strip().lower()
    if t == "select":
        s = p.get("select") or {}
        return (s.get("name") or "").strip().lower()
    return ""


def _parse_expires(expires_at_str: str) -> date | None:
    """
    expires_at хранится как TEXT 'YYYY-MM-DD'
    """
    if not expires_at_str:
        return None
    try:
        return datetime.strptime(expires_at_str.strip(), "%Y-%m-%d").date()
    except Exception:
        return None


async def get_latest_request_for_user(tg_id: int) -> dict | None:
    """
    Берём ПОСЛЕДНЮЮ заявку пользователя (любого статуса).
    """
    tg_id_str = str(tg_id)
    filter_obj = {"property": "tg_id", "rich_text": {"equals": tg_id_str}}

    log.info("Cabinet Notion fetch start. tg_id=%s", tg_id_str)
    data = await notion_query_database(filter_obj, page_size=10)
    results = data.get("results", [])
    page = results[0] if results else None
    log.info("Cabinet Notion fetch done. tg_id=%s found=%s", tg_id_str, bool(page))
    return page

# =========================
# HELPERS
# =========================

def expires_from_key(key: str) -> str:
    months = int(PERIOD_MONTHS[key])
    return (datetime.utcnow() + relativedelta(months=months)).strftime("%Y-%m-%d")


def build_tally_url(params: dict) -> str:
    params = dict(params)
    params["_tail"] = "1"
    query = urlencode(params, quote_via=quote)
    return f"{TALLY_FORM_URL}?{query}"


async def send_photo_safe(message: Message, path: str, caption: str | None = None, reply_markup=None):
    try:
        photo = FSInputFile(path)
        await message.answer_photo(photo=photo, caption=caption, reply_markup=reply_markup)
    except TelegramNetworkError as e:
        log.warning(
            "TelegramNetworkError on answer_photo. path=%s chat_id=%s user_id=%s err=%s",
            path, getattr(message.chat, "id", None), getattr(message.from_user, "id", None), repr(e),
        )
        await safe_answer(message, caption or " ", reply_markup=reply_markup)
    except Exception as e:
        log.warning(
            "answer_photo failed, fallback to text. path=%s err=%s",
            path, repr(e),
        )
        await safe_answer(message, caption or " ", reply_markup=reply_markup)


def tally_confirm_kb(tally_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подтверждение оплаты", web_app=WebAppInfo(url=tally_url))]
    ])


async def send_payment_flow_final(
    message: Message,
    *,
    tg_id: int,
    tg_username: str,
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
        "t": str(tg_id),
        "u": tg_username or "",
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

    log.info(
        "Payment flow init. tg_id=%s product=%s pay_method=%s currency=%s amount=%s period_key=%s",
        tg_id, product, pay_method, currency, amount, period_key,
    )

    if currency == "USDT":
        await safe_answer(message, f"Для оплаты Вам необходимо перевести {amount} USDT:")
        await safe_answer(message, f"<code>{USDT_TRC20_ADDRESS}</code> (USDT. Сеть TRC20)", reply_markup=kb)
    else:
        await safe_answer(message, f"Для оплаты Вам необходимо перевести {amount} грн на указанные реквизиты:")
        await safe_answer(message, "Скоро добавим карту.", reply_markup=kb)

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


def cabinet_refresh_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обновить", callback_data="cabinet:refresh")]
    ])

# =========================
# CABINET TEXT BUILDER
# =========================

async def build_cabinet_text(user_id: int) -> str:
    discord = "Не указан"
    email = "Не указан"
    status_text = "Нет активной подписки"

    page = await get_latest_request_for_user(user_id)
    if page:
        props = page.get("properties", {})
        st = _status_name(props, "status")
        expires_raw = _rt_plain(props, "expires_at")
        expires_dt = _parse_expires(expires_raw)

        if st == "pending":
            status_text = "Заявка на проверке"
        elif st == "rejected":
            status_text = f"Заявка отклонена. Свяжитесь с администратором: {ADMIN_USERNAME}"
        elif st == "approved":
            d = _rt_plain(props, "discord")
            e = _rt_plain(props, "email")
            if d:
                discord = d
            if e:
                email = e

            if expires_dt:
                if expires_dt >= date.today():
                    status_text = f"Активна до: {expires_dt.isoformat()}"
                else:
                    status_text = f"Подписка истекла: {expires_dt.isoformat()}"
            else:
                status_text = "Активна (дата не указана)"
        else:
            status_text = "Заявка на проверке"

    return (
        "👤 Личный кабинет\n\n"
        f"Discord: <b>{discord}</b>\n"
        f"Email: <b>{email}</b>\n\n"
        f"Статус: <b>{status_text}</b>"
    )


async def send_cabinet(message: Message, user_id: int):
    """
    Единая функция отправки кабинета с кнопкой "Обновить".
    """
    try:
        t0 = time.perf_counter()
        text = await build_cabinet_text(user_id)
        ms = int((time.perf_counter() - t0) * 1000)
        log.info("Cabinet build OK (%sms). user_id=%s", ms, user_id)
        await safe_answer(message, text, reply_markup=cabinet_refresh_kb())

    except (httpx.TimeoutException, TelegramNetworkError) as e:
        log.warning("Cabinet temporary error. user_id=%s err=%s", user_id, repr(e))
        await safe_answer(message, CABINET_RETRY_TEXT)

    except Exception as e:
        log.exception("Cabinet unexpected error. user_id=%s err=%s", user_id, repr(e))
        await safe_answer(message, f"Ошибка кабинета: {e}")

# =========================
# HANDLERS
# =========================

@dp.message(CommandStart())
async def start(message: Message):
    log.info("START /start. user_id=%s username=%s", message.from_user.id, message.from_user.username)
    await safe_answer(message, WELCOME_TEXT, reply_markup=main_menu_kb())


@dp.message(Command("menu"))
async def menu(message: Message):
    log.info("CMD /menu. user_id=%s", message.from_user.id)
    await safe_answer(message, "Главное меню 👇", reply_markup=main_menu_kb())


@dp.message(lambda m: (m.text or "") == "В главное меню")
async def back_to_main_menu(message: Message):
    log.info("Back to main menu. user_id=%s", message.from_user.id)
    await safe_answer(message, "Главное меню", reply_markup=main_menu_kb())


@dp.message(lambda m: "Информация" in (m.text or ""))
async def info_from_menu(message: Message):
    log.info("Info tapped. user_id=%s", message.from_user.id)
    await safe_answer(message, "ℹ️ Раздел «Информация» пока в разработке.")


@dp.message(lambda m: "Помощь" in (m.text or ""))
async def help_from_menu(message: Message):
    log.info("Help tapped. user_id=%s", message.from_user.id)
    await safe_answer(message, "❓ Раздел «Помощь» пока в разработке.")


@dp.message(lambda m: "Мои ресурсы" in (m.text or ""))
async def resources_from_menu(message: Message):
    log.info("Resources tapped. user_id=%s", message.from_user.id)
    await send_photo_safe(
        message,
        RESOURCES_IMAGE_PATH,
        caption="Подписывайтесь ⬇️⬇️⬇️",
        reply_markup=resources_links_kb(),
    )
    await safe_answer(message, "Чтобы вернуться, нажмите «В главное меню».", reply_markup=resources_back_kb())


@dp.message(lambda m: "Мои продукты" in (m.text or ""))
async def products_entry(message: Message):
    log.info("Products tapped. user_id=%s", message.from_user.id)
    await send_photo_safe(message, PRODUCTS_IMAGE_PATH, caption=None)
    await safe_answer(message, "Выберите:", reply_markup=products_menu_kb())


@dp.message(F.text == "Hadiukov Community")
async def community_info(message: Message):
    log.info("Product: Community. user_id=%s", message.from_user.id)
    await safe_answer(message, "Объяснение внутрянки сервера", reply_markup=kb_community_buy())


@dp.message(F.text == "Hadiukov Mentoring")
async def mentoring_info(message: Message):
    log.info("Product: Mentoring. user_id=%s", message.from_user.id)
    await safe_answer(message, "Объяснение того что будет на менторке", reply_markup=kb_mentoring_buy())


@dp.message(lambda m: "Личный кабинет" in (m.text or ""))
async def cabinet_from_menu(message: Message):
    log.info("Cabinet tapped. user_id=%s", message.from_user.id)
    await send_cabinet(message, message.from_user.id)


@dp.callback_query(F.data == "cabinet:refresh")
async def cabinet_refresh(cb: CallbackQuery):
    log.info("Cabinet refresh tapped. user_id=%s", cb.from_user.id)
    try:
        await cb.message.delete()
    except Exception as e:
        log.warning("Cabinet refresh: failed to delete old message. err=%s", repr(e))

    await send_cabinet(cb.message, cb.from_user.id)
    await cb.answer()


# --- Inline: Buy / Acquire ---
@dp.callback_query(F.data == "buy:community")
async def buy_community(cb: CallbackQuery):
    log.info("Buy community. user_id=%s", cb.from_user.id)
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
    log.info("Buy mentoring. user_id=%s", cb.from_user.id)
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
    log.info("Payment method choice. user_id=%s product=%s method=%s", cb.from_user.id, product_key, method)

    if product_key == "community" and method == "crypto":
        await send_photo_safe(cb.message, SUBSCRIPTION_IMAGE_PATH, "Выберите срок подписки", kb_community_crypto_periods())
    elif product_key == "community" and method == "fiat":
        await send_photo_safe(cb.message, SUBSCRIPTION_IMAGE_PATH, "Выберите срок подписки", kb_community_fiat_periods())
    elif product_key == "mentoring" and method == "crypto":
        await send_photo_safe(cb.message, SUBSCRIPTION_IMAGE_PATH, "Выберите срок подписки", kb_mentoring_crypto())
    elif product_key == "mentoring" and method == "fiat":
        await send_photo_safe(cb.message, SUBSCRIPTION_IMAGE_PATH, "Выберите срок подписки", kb_mentoring_fiat())

    await cb.answer()


@dp.callback_query(F.data == "close")
async def close_message(cb: CallbackQuery):
    log.info("Close message. user_id=%s", cb.from_user.id)
    await cb.message.delete()
    await cb.answer()


@dp.callback_query(F.data.startswith("sub:"))
async def subscription_selected(cb: CallbackQuery):
    _, product_key, method, choice = cb.data.split(":")

    user_id = cb.from_user.id
    user_username = cb.from_user.username or ""

    log.info("Subscription selected. user_id=%s product=%s method=%s choice=%s", user_id, product_key, method, choice)

    if product_key == "community":
        product_name = "Hadiukov Community"
        period_key = choice if choice in ("1m", "3m") else ""
        period_text = PERIOD_TEXT.get(period_key, "")
        expires_at = expires_from_key(period_key) if period_key else ""

        if method == "crypto":
            amount = COMMUNITY_USDT_1M if choice == "1m" else COMMUNITY_USDT_3M
            await send_payment_flow_final(
                cb.message,
                tg_id=user_id,
                tg_username=user_username,
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
                tg_id=user_id,
                tg_username=user_username,
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
                tg_id=user_id,
                tg_username=user_username,
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
                tg_id=user_id,
                tg_username=user_username,
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
    log.info("Bot starting polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
