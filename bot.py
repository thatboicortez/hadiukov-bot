import uuid
import asyncio
import logging
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
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter

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

ADMIN_USERNAME = "@trademark830am"

# Resources links
YOUTUBE_URL = "https://youtube.com/@hadiukov?si=vy9gXXiLKeDYIfR_"
INSTAGRAM_URL = "https://www.instagram.com/hadiukov?igsh=MTdtZmp4MmtxdzF2dw=="
TELEGRAM_URL = "https://t.me/hadiukov"

# Mentoring Tally (заявка)
MENTORING_TALLY_URL = "https://tally.so/r/68KqNN"

# Images (пути в репо)
COMMUNITY_IMAGE_PATH = "pictures/community.png"
RESOURCES_IMAGE_PATH = "pictures/resources.png"
PRODUCTS_IMAGE_PATH = "pictures/products.png"
PAYMENT_IMAGE_PATH = "pictures/payment.png"
SUBSCRIPTION_IMAGE_PATH = "pictures/subscription.png"
SUPPORT_IMAGE_PATH = "pictures/support.png"  # <-- добавили

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
# SAFE SEND (Telegram retry)
# =========================

async def safe_answer(message: Message, text: str, *, reply_markup=None, retries: int = 3):
    """
    Надёжная отправка сообщений:
    - TelegramNetworkError: повторяем
    - TelegramRetryAfter (Flood control): ждём и повторяем
    """
    last_err = None
    for attempt in range(retries):
        try:
            return await message.answer(text, reply_markup=reply_markup)
        except TelegramRetryAfter as e:
            last_err = e
            wait_s = float(getattr(e, "retry_after", 2.0))
            log.warning("TelegramRetryAfter: wait %.2fs (attempt %s/%s)", wait_s, attempt + 1, retries)
            await asyncio.sleep(wait_s)
        except TelegramNetworkError as e:
            last_err = e
            await asyncio.sleep(1.0 + attempt * 0.5)
        except Exception as e:
            last_err = e
            break
    log.error("safe_answer failed: %r", last_err)
    return None


async def safe_cb_answer(cb: CallbackQuery, *, retries: int = 3):
    """
    Чтобы всегда гасить 'loading...' на инлайн кнопках.
    """
    last_err = None
    for attempt in range(retries):
        try:
            await cb.answer()
            return
        except TelegramRetryAfter as e:
            last_err = e
            wait_s = float(getattr(e, "retry_after", 2.0))
            log.warning("cb.answer TelegramRetryAfter: wait %.2fs (attempt %s/%s)", wait_s, attempt + 1, retries)
            await asyncio.sleep(wait_s)
        except TelegramNetworkError as e:
            last_err = e
            await asyncio.sleep(1.0 + attempt * 0.5)
        except Exception as e:
            last_err = e
            break
    log.error("safe_cb_answer failed: %r", last_err)


# =========================
# NOTION (READ ONLY)
# =========================

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


async def notion_query_database(filter_obj: dict, page_size: int = 10, max_attempts: int = 4) -> dict:
    """
    Query Notion DB с ретраями + backoff.
    Ретраим:
      - timeout / transport errors
      - 429 (rate limit)
      - 5xx
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

    base_delay = 0.7
    timeout = httpx.Timeout(30.0, connect=10.0)

    last_err = None
    for attempt in range(1, max_attempts + 1):
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(url, headers=headers, json=payload)

            dt_ms = int((time.perf_counter() - t0) * 1000)

            # ретраи на 429 / 5xx
            if r.status_code == 429 or 500 <= r.status_code <= 599:
                retry_after = r.headers.get("Retry-After")
                if retry_after:
                    sleep_s = float(retry_after)
                else:
                    sleep_s = base_delay * (2 ** (attempt - 1))
                log.warning(
                    "Notion query retryable status=%s (%sms) attempt=%s/%s sleep=%.2fs",
                    r.status_code, dt_ms, attempt, max_attempts, sleep_s
                )
                await asyncio.sleep(sleep_s)
                continue

            r.raise_for_status()

            log.info("Notion query OK (%sms) attempt=%s/%s", dt_ms, attempt, max_attempts)
            return r.json()

        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_err = e
            sleep_s = base_delay * (2 ** (attempt - 1))
            log.warning("Notion query network/timeout: %r attempt=%s/%s sleep=%.2fs", e, attempt, max_attempts, sleep_s)
            await asyncio.sleep(sleep_s)
        except httpx.HTTPStatusError as e:
            last_err = e
            log.error("Notion query HTTPStatusError: %s", str(e))
            raise
        except Exception as e:
            last_err = e
            log.error("Notion query unknown error: %r", e)
            raise

    log.error("Notion query failed after %s attempts: %r", max_attempts, last_err)
    raise last_err


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
    data = await notion_query_database(filter_obj, page_size=10)
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


async def send_photo_safe(message: Message, path: str, caption: str | None = None, reply_markup=None):
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


def mentoring_apply_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оставить заявку", web_app=WebAppInfo(url=MENTORING_TALLY_URL))]
    ])


def admin_contact_kb() -> InlineKeyboardMarkup:
    admin = ADMIN_USERNAME.lstrip("@")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Написать", url=f"https://t.me/{admin}")]
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


def kb_mentoring_apply() -> InlineKeyboardMarkup:
    return mentoring_apply_kb()


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

HELP_TEXT = (
    "Если что-то непонятно при оформлении подписки или оплате — напишите администратору.\n"
    "Он поможет разобраться и подскажет, что делать дальше."
)

# =========================
# CABINET TEXT BUILDER (UPDATED)
# =========================

async def build_cabinet_text(user_id: int) -> str:
    discord = "Не указан"
    email = "Не указан"

    page = await get_latest_request_for_user(user_id)
    if not page:
        return (
            f"Discord: {discord}\n"
            f"Email: {email}\n\n"
            "Нет активной подписки"
        )

    props = page.get("properties", {})
    st = _status_name(props, "status")

    d = _rt_plain(props, "discord")
    e = _rt_plain(props, "email")
    if d:
        discord = d
    if e:
        email = e

    expires_raw = _rt_plain(props, "expires_at")
    expires_dt = _parse_expires(expires_raw)

    if st == "pending":
        status_line = "Заявка на проверке"
    elif st == "rejected":
        status_line = f"Заявка отклонена. Свяжитесь с администратором: {ADMIN_USERNAME}"
    elif st == "approved":
        if expires_dt:
            if expires_dt >= date.today():
                status_line = f"<b>Подписка активна до: {expires_dt.isoformat()}</b>"
            else:
                status_line = f"<b>Подписка истекла: {expires_dt.isoformat()}</b>"
        else:
            status_line = "<b>Подписка активна</b>"
    else:
        status_line = "Заявка на проверке"

    return (
        f"Discord: {discord}\n"
        f"Email: {email}\n\n"
        f"{status_line}"
    )


async def send_cabinet(message: Message, user_id: int):
    try:
        t0 = time.perf_counter()
        log.info("Cabinet tapped. user_id=%s", user_id)

        text = await build_cabinet_text(user_id)

        dt_ms = int((time.perf_counter() - t0) * 1000)
        log.info("Cabinet build OK (%sms). user_id=%s", dt_ms, user_id)

        await safe_answer(message, text, reply_markup=cabinet_refresh_kb())
    except (httpx.TimeoutException, TelegramNetworkError):
        await safe_answer(message, CABINET_RETRY_TEXT)
    except Exception as e:
        log.exception("Cabinet error user_id=%s", user_id)
        await safe_answer(message, f"Ошибка кабинета: {e}")


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
    await safe_answer(message, """Hadiukov Community – это среда, где результат строится на дисциплине, ясной системе и умении подстраиваться под рынок.

Мы не ищем «секретные кнопки» и не торгуем эмоциями. Здесь фокус на том, что реально повышает качество трейдинга:
 • понятная логика работы с движением цены и контекстом;
 • стабильный процесс: планы, правила, исполнение;
 • регулярный разбор сделок и контроль ошибок;
 • прокачка мышления и устойчивости под нагрузкой.

Этот подход помогает выстроить профессиональную базу: видеть, что именно приносит деньги, что тянет вниз, и как шаг за шагом усиливать свой перформанс без хаоса и угадываний.

Если тебе близка торговля как работа, а не как азарт – добро пожаловать в Hadiukov Community.""")


@dp.message(lambda m: "Помощь" in (m.text or ""))
async def help_from_menu(message: Message):
    # 1) Фото + подпись (caption) + inline кнопка "Написать"
    await send_photo_safe(
        message,
        SUPPORT_IMAGE_PATH,
        caption=HELP_TEXT,
        reply_markup=admin_contact_kb(),
    )

    # 2) Меняем нижнюю клавиатуру на одну кнопку "В главное меню"
    await safe_answer(
        message,
        "Чтобы вернуться, нажмите «В главное меню».",
        reply_markup=resources_back_kb(),
    )


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
    await send_photo_safe(
        message,
        COMMUNITY_IMAGE_PATH,
        caption="""Я ежедневно выполняю свою рутину – торговые планы, аналитика, статистика, сделки.
В Discord я просто делюсь этим процессом в реальном времени, без задержек и в спокойной обстановке.

Это не обучение и не “инфо-помойка”. Нет десятков веток, методичек и бесконечных уроков. Сервер собран только под практику. Я показываю, как сам работаю.

Внутри – рутинная работа и поддержка среды:
• анализ графиков
• бэктесты
• итоги недели / месяца / квартала
• стримы с ответами на вопросы
• разбор рыночных ситуаций
• развитие сильного майнд-сета

Суть сервера – выстроить рабочий алгоритм и быть в адекватной среде, где все нацелены на результат и процесс.""",
        reply_markup=kb_community_buy(),
    )


@dp.message(F.text == "Hadiukov Mentoring")
async def mentoring_info(message: Message):
    await safe_answer(message, "Объяснение того что будет на менторке", reply_markup=kb_mentoring_apply())


@dp.message(lambda m: "Личный кабинет" in (m.text or ""))
async def cabinet_from_menu(message: Message):
    await send_cabinet(message, message.from_user.id)


@dp.callback_query(F.data == "cabinet:refresh")
async def cabinet_refresh(cb: CallbackQuery):
    try:
        await cb.message.delete()
    except Exception:
        pass

    await send_cabinet(cb.message, cb.from_user.id)
    await safe_cb_answer(cb)


@dp.callback_query(F.data == "buy:community")
async def buy_community(cb: CallbackQuery):
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
    await safe_cb_answer(cb)


@dp.callback_query(F.data == "buy:mentoring")
async def buy_mentoring_legacy(cb: CallbackQuery):
    try:
        await cb.message.delete()
    except Exception:
        pass

    await safe_answer(cb.message, "Объяснение того что будет на менторке", reply_markup=kb_mentoring_apply())
    await safe_cb_answer(cb)


@dp.callback_query(F.data.startswith("pm:"))
async def payment_method_choice(cb: CallbackQuery):
    _, product_key, method = cb.data.split(":")

    if product_key == "mentoring":
        await safe_answer(cb.message, "Объяснение того что будет на менторке", reply_markup=kb_mentoring_apply())
        await safe_cb_answer(cb)
        return

    if product_key == "community" and method == "crypto":
        await send_photo_safe(cb.message, SUBSCRIPTION_IMAGE_PATH, "Выберите срок подписки", kb_community_crypto_periods())
    elif product_key == "community" and method == "fiat":
        await send_photo_safe(cb.message, SUBSCRIPTION_IMAGE_PATH, "Выберите срок подписки", kb_community_fiat_periods())

    await safe_cb_answer(cb)


@dp.callback_query(F.data == "close")
async def close_message(cb: CallbackQuery):
    try:
        await cb.message.delete()
    except Exception:
        pass
    await safe_cb_answer(cb)


@dp.callback_query(F.data.startswith("sub:"))
async def subscription_selected(cb: CallbackQuery):
    _, product_key, method, choice = cb.data.split(":")

    user_id = cb.from_user.id
    user_username = cb.from_user.username or ""

    if product_key == "mentoring":
        await safe_answer(cb.message, "Объяснение того что будет на менторке", reply_markup=kb_mentoring_apply())
        await safe_cb_answer(cb)
        return

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

    await safe_cb_answer(cb)


# =========================
# RUN
# =========================

async def main():
    log.info("Bot starting polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
