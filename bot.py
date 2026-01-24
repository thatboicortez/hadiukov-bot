import uuid
import asyncio
from datetime import datetime, date
from urllib.parse import urlencode, quote
from typing import Optional, Dict, Tuple

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

# =========================
# BOT INIT
# =========================

bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# =========================
# HTTP CLIENT (GLOBAL, KEEP-ALIVE)
# =========================

_http: Optional[httpx.AsyncClient] = None

def get_http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        # Быстрые таймауты для “живости” + keep-alive пул
        timeout = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)
        limits = httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=30.0)
        _http = httpx.AsyncClient(timeout=timeout, limits=limits, http2=False, follow_redirects=True)
    return _http


async def on_shutdown():
    global _http
    if _http is not None:
        await _http.aclose()
        _http = None

dp.shutdown.register(on_shutdown)

# =========================
# SAFE SEND (Telegram retry)
# =========================

async def safe_answer(message: Message, text: str, *, reply_markup=None, retries: int = 2):
    for _ in range(retries):
        try:
            return await message.answer(text, reply_markup=reply_markup)
        except TelegramNetworkError:
            await asyncio.sleep(0.7)
    return None


async def safe_send(chat_id: int, text: str, *, reply_markup=None, retries: int = 2):
    for _ in range(retries):
        try:
            return await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        except TelegramNetworkError:
            await asyncio.sleep(0.7)
    return None


async def safe_edit_text(message: Message, text: str, *, reply_markup=None):
    try:
        return await message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        # если нельзя edit (например, сообщение уже не твоё/старое) — просто отправим новое
        return await safe_send(message.chat.id, text, reply_markup=reply_markup)


# =========================
# NOTION (READ ONLY)
# =========================

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def notion_headers() -> dict:
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


async def notion_query_database(filter_obj: dict, page_size: int = 1) -> dict:
    """
    Query Notion DB with shared http client (keep-alive).
    page_size=1 потому что ты берёшь только последнюю запись.
    """
    url = f"{NOTION_API_BASE}/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": filter_obj,
        "page_size": page_size,
        "sorts": [{"timestamp": "created_time", "direction": "descending"}],
    }

    client = get_http()
    r = await client.post(url, headers=notion_headers(), json=payload)
    r.raise_for_status()
    return r.json()


def _rt_plain(props: dict, prop_name: str) -> str:
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


def _parse_expires(expires_at_str: str) -> Optional[date]:
    if not expires_at_str:
        return None
    try:
        return datetime.strptime(expires_at_str.strip(), "%Y-%m-%d").date()
    except Exception:
        return None


async def get_latest_request_for_user(tg_id: int) -> Optional[dict]:
    tg_id_str = str(tg_id)
    filter_obj = {"property": "tg_id", "rich_text": {"equals": tg_id_str}}
    data = await notion_query_database(filter_obj, page_size=1)
    results = data.get("results", [])
    return results[0] if results else None


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


# Файлы можно держать как path — FSInputFile лёгкий, создаём при отправке.
async def send_photo_safe(message: Message, path: str, caption: Optional[str] = None, reply_markup=None):
    try:
        photo = FSInputFile(path)
        await message.answer_photo(photo=photo, caption=caption, reply_markup=reply_markup)
    except TelegramNetworkError:
        await safe_answer(message, caption or " ", reply_markup=reply_markup)
    except Exception:
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
# TEXTS
# =========================

WELCOME_TEXT = (
    "Вас приветствует Sever by Hadiukov!\n\n"
    "Сейчас вы находитесь в официальном боте проекта.\n"
    "Здесь вы можете оформить или продлить подписку и отправить подтверждение оплаты.\n\n"
    "Выберите нужный раздел в меню снизу 👇\n"
    f"Если возникнут вопросы — напишите администратору {ADMIN_USERNAME}."
)

CABINET_RETRY_TEXT = "⏳ Подожди 10–20 секунд и нажми «Личный кабинет» ещё раз."
CABINET_LOADING_TEXT = "⏳ Загружаю личный кабинет..."


# =========================
# CABINET FAST: CACHE + LOCK + BACKGROUND UPDATE
# =========================

# cache: user_id -> (timestamp_monotonic, text)
_cabinet_cache: Dict[int, Tuple[float, str]] = {}
_user_locks: Dict[int, asyncio.Lock] = {}

def _lock_for(user_id: int) -> asyncio.Lock:
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


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


async def update_cabinet_message(message: Message, user_id: int, *, force: bool = False):
    """
    Обновляет ОДНО сообщение: сначала "loading", потом edit на реальный текст.
    Делается под lock, с кэшем на 3 секунды.
    """
    async with _lock_for(user_id):
        now = asyncio.get_running_loop().time()

        if not force:
            cached = _cabinet_cache.get(user_id)
            if cached and (now - cached[0] <= 3.0):
                await safe_edit_text(message, cached[1], reply_markup=cabinet_refresh_kb())
                return

        # показываем loading (и не ждём Notion в UI)
        await safe_edit_text(message, CABINET_LOADING_TEXT, reply_markup=cabinet_refresh_kb())

        try:
            text = await build_cabinet_text(user_id)
            _cabinet_cache[user_id] = (now, text)
            await safe_edit_text(message, text, reply_markup=cabinet_refresh_kb())
        except (httpx.TimeoutException, TelegramNetworkError):
            await safe_edit_text(message, CABINET_RETRY_TEXT, reply_markup=cabinet_refresh_kb())
        except Exception as e:
            await safe_edit_text(message, f"Ошибка кабинета: {e}", reply_markup=cabinet_refresh_kb())


def run_bg(coro):
    async def _runner():
        try:
            await coro
        except Exception:
            pass
    asyncio.create_task(_runner())


# =========================
# HANDLERS
# =========================

@dp.message(CommandStart())
async def start(message: Message):
    await safe_answer(message, WELCOME_TEXT, reply_markup=main_menu_kb())


@dp.message(Command("menu"))
async def menu(message: Message):
    await safe_answer(message, "Главное меню 👇", reply_markup=main_menu_kb())


@dp.message(lambda m: (m.text or "") == "В главное меню")
async def back_to_main_menu(message: Message):
    await safe_answer(message, "Главное меню", reply_markup=main_menu_kb())


@dp.message(lambda m: "Информация" in (m.text or ""))
async def info_from_menu(message: Message):
    await safe_answer(message, "ℹ️ Раздел «Информация» пока в разработке.")


@dp.message(lambda m: "Помощь" in (m.text or ""))
async def help_from_menu(message: Message):
    await safe_answer(message, "❓ Раздел «Помощь» пока в разработке.")


@dp.message(lambda m: "Мои ресурсы" in (m.text or ""))
async def resources_from_menu(message: Message):
    await send_photo_safe(
        message,
        RESOURCES_IMAGE_PATH,
        caption="Подписывайтесь ⬇️⬇️⬇️",
        reply_markup=resources_links_kb(),
    )
    await safe_answer(message, "Чтобы вернуться, нажмите «В главное меню».", reply_markup=resources_back_kb())


@dp.message(lambda m: "Мои продукты" in (m.text or ""))
async def products_entry(message: Message):
    await send_photo_safe(message, PRODUCTS_IMAGE_PATH, caption=None)
    await safe_answer(message, "Выберите:", reply_markup=products_menu_kb())


@dp.message(F.text == "Hadiukov Community")
async def community_info(message: Message):
    await safe_answer(message, "Объяснение внутрянки сервера", reply_markup=kb_community_buy())


@dp.message(F.text == "Hadiukov Mentoring")
async def mentoring_info(message: Message):
    await safe_answer(message, "Объяснение того что будет на менторке", reply_markup=kb_mentoring_buy())


@dp.message(lambda m: "Личный кабинет" in (m.text or ""))
async def cabinet_from_menu(message: Message):
    # мгновенно отдаём сообщение, а Notion грузим в фоне и редактируем это же сообщение
    m = await safe_answer(message, CABINET_LOADING_TEXT, reply_markup=cabinet_refresh_kb())
    if m:
        run_bg(update_cabinet_message(m, message.from_user.id, force=True))


@dp.callback_query(F.data == "cabinet:refresh")
async def cabinet_refresh(cb: CallbackQuery):
    # ✅ ВАЖНО: ack в первой строке, без try/except
    await cb.answer()

    if cb.message:
        # мгновенно: просто запускаем обновление в фоне
        run_bg(update_cabinet_message(cb.message, cb.from_user.id, force=True))


# --- Inline: Buy / Acquire ---
@dp.callback_query(F.data == "buy:community")
async def buy_community(cb: CallbackQuery):
    await cb.answer()

    try:
        await cb.message.delete()
    except Exception:
        pass

    await send_photo_safe(
        cb.message,
        PAYMENT_IMAGE_PATH,
        caption="Выберите способ оплаты",
        reply_markup=kb_payment_methods("community"),
    )


@dp.callback_query(F.data == "buy:mentoring")
async def buy_mentoring(cb: CallbackQuery):
    await cb.answer()

    try:
        await cb.message.delete()
    except Exception:
        pass

    await send_photo_safe(
        cb.message,
        PAYMENT_IMAGE_PATH,
        caption="Выберите способ оплаты",
        reply_markup=kb_payment_methods("mentoring"),
    )


@dp.callback_query(F.data.startswith("pm:"))
async def payment_method_choice(cb: CallbackQuery):
    await cb.answer()

    _, product_key, method = cb.data.split(":")

    if product_key == "community" and method == "crypto":
        await send_photo_safe(cb.message, SUBSCRIPTION_IMAGE_PATH, "Выберите срок подписки", kb_community_crypto_periods())
    elif product_key == "community" and method == "fiat":
        await send_photo_safe(cb.message, SUBSCRIPTION_IMAGE_PATH, "Выберите срок подписки", kb_community_fiat_periods())
    elif product_key == "mentoring" and method == "crypto":
        await send_photo_safe(cb.message, SUBSCRIPTION_IMAGE_PATH, "Выберите срок подписки", kb_mentoring_crypto())
    elif product_key == "mentoring" and method == "fiat":
        await send_photo_safe(cb.message, SUBSCRIPTION_IMAGE_PATH, "Выберите срок подписки", kb_mentoring_fiat())


@dp.callback_query(F.data == "close")
async def close_message(cb: CallbackQuery):
    await cb.answer()
    try:
        await cb.message.delete()
    except Exception:
        pass


@dp.callback_query(F.data.startswith("sub:"))
async def subscription_selected(cb: CallbackQuery):
    await cb.answer()

    _, product_key, method, choice = cb.data.split(":")

    user_id = cb.from_user.id
    user_username = cb.from_user.username or ""

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


# =========================
# RUN
# =========================

async def main():
    # прогреем клиент (опционально, но часто ускоряет первый Notion-запрос)
    get_http()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
