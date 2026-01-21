import asyncio
import logging
import urllib.parse
from datetime import datetime, date
from dateutil.relativedelta import relativedelta

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)

from config import (
    BOT_TOKEN,
    NOTION_TOKEN,
    NOTION_DATABASE_ID,
    NOTION_VERSION,
    TALLY_FORM_URL,
    PRODUCT_NAME,
    USDT_TRC20_ADDRESS,
    PLANS,
    PAY_METHOD_DEFAULT,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("hadiukov-bot")

router = Router()


# ---------------------------
# Keyboards
# ---------------------------

def main_menu_kb() -> ReplyKeyboardMarkup:
    # Плитки всегда видны (как ты хотел)
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
    )


def plans_kb() -> InlineKeyboardMarkup:
    buttons = []
    for key, p in PLANS.items():
        buttons.append([InlineKeyboardButton(text=p["label"], callback_data=f"plan:{key}")])
    buttons.append([InlineKeyboardButton(text="Закрыть", callback_data="close")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------
# Notion client helpers
# ---------------------------

class NotionClient:
    def __init__(self, token: str, database_id: str):
        self.token = token
        self.database_id = database_id
        self.session: aiohttp.ClientSession | None = None

    async def start(self):
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=20)
            self.session = aiohttp.ClientSession(timeout=timeout)

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    async def query_by_tg_id(self, tg_id: str) -> list[dict]:
        """
        Ищем все записи в базе по tg_id (rich_text).
        """
        if not self.token or not self.database_id:
            return []

        await self.start()
        assert self.session is not None

        url = f"https://api.notion.com/v1/databases/{self.database_id}/query"
        payload = {
            "filter": {
                "property": "tg_id",
                "rich_text": {"equals": str(tg_id)},
            },
            "sorts": [
                {"timestamp": "created_time", "direction": "descending"}
            ],
            "page_size": 50,
        }

        async with self.session.post(url, headers=self._headers(), json=payload) as r:
            if r.status != 200:
                txt = await r.text()
                log.warning("Notion query error %s: %s", r.status, txt)
                return []
            data = await r.json()
            return data.get("results", []) or []


def get_rich_text(prop: dict) -> str:
    """
    Для Notion Text (rich_text) вытаскиваем plain_text.
    """
    if not prop:
        return ""
    rt = prop.get("rich_text") or []
    if not rt:
        return ""
    return (rt[0].get("plain_text") or "").strip()


def get_status_name(prop: dict) -> str:
    """
    Для Notion Status.
    """
    if not prop:
        return ""
    st = prop.get("status")
    if not st:
        return ""
    return (st.get("name") or "").strip().lower()


def parse_expires_at(text: str) -> date | None:
    """
    expires_at у тебя Text. Обычно приходит YYYY-MM-DD.
    Поддержим несколько вариантов.
    """
    if not text:
        return None
    text = text.strip()

    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


async def resolve_cabinet(notion: NotionClient, tg_id: str) -> dict:
    """
    Возвращает данные для кабинета по правилам:
    - показываем Discord/Email ТОЛЬКО если есть approved и подписка не истекла
    - иначе: Не указан
    - статус: active / pending / none / expired
    """
    pages = await notion.query_by_tg_id(tg_id)

    # если вообще ничего нет
    if not pages:
        return {
            "discord": "",
            "email": "",
            "status": "none",
            "expires_at": None,
        }

    # проверим: есть ли pending
    has_pending = False
    for p in pages:
        props = p.get("properties", {})
        if get_status_name(props.get("status")) == "pending":
            has_pending = True
            break

    # ищем последнюю approved, у которой expires_at >= today
    today = date.today()
    for p in pages:
        props = p.get("properties", {})

        st = get_status_name(props.get("status"))
        if st != "approved":
            continue

        expires_text = get_rich_text(props.get("expires_at"))
        expires_dt = parse_expires_at(expires_text)

        # если expires_at пустой — считаем что не активна (лучше, чем случайно дать доступ)
        if not expires_dt:
            continue

        if expires_dt >= today:
            discord = get_rich_text(props.get("discord"))
            email = get_rich_text(props.get("email"))
            return {
                "discord": discord,
                "email": email,
                "status": "active",
                "expires_at": expires_dt,
            }

    # если нашли pending, но approved нет
    if has_pending:
        return {
            "discord": "",
            "email": "",
            "status": "pending",
            "expires_at": None,
        }

    # иначе: либо expired, либо rejected/что-то ещё
    return {
        "discord": "",
        "email": "",
        "status": "none",
        "expires_at": None,
    }


# ---------------------------
# Tally URL builder (prefill hidden fields)
# ---------------------------

def build_tally_url(
    base_url: str,
    tg_id: int,
    tg_username: str,
    plan_key: str,
) -> str:
    """
    В Tally у тебя поля называются коротко:
      t  -> tg_id
      u  -> tg_username
      pk -> period_key
      as -> amount_usdt
      au -> amount_uah
      pm -> pay_method
      ex -> expires_at
      product / period — тоже можно префиллить если они у тебя есть в форме
    """
    plan = PLANS[plan_key]

    expires = (date.today() + relativedelta(months=plan["months"])).strftime("%Y-%m-%d")

    params = {
        "t": str(tg_id),
        "u": (tg_username or "").lstrip("@"),
        "pk": plan_key,
        "as": plan["amount_usdt"],
        "au": plan["amount_uah"],
        "pm": PAY_METHOD_DEFAULT,
        "product": PRODUCT_NAME,
        "period": plan["label"],
        "ex": expires,
    }

    # аккуратно добавим query к базовой ссылке
    parsed = urllib.parse.urlparse(base_url)
    q = dict(urllib.parse.parse_qsl(parsed.query))
    q.update(params)

    new_query = urllib.parse.urlencode(q)
    rebuilt = parsed._replace(query=new_query)
    return urllib.parse.urlunparse(rebuilt)


# ---------------------------
# Handlers
# ---------------------------

@router.message(CommandStart())
async def start(message: Message):
    await message.answer("Главное меню", reply_markup=main_menu_kb())


@router.message(F.text == "ℹ️ Информация")
async def info(message: Message):
    await message.answer("Раздел «Информация» пока в разработке.", reply_markup=main_menu_kb())


@router.message(F.text == "❓ Помощь")
async def help_cmd(message: Message):
    await message.answer("Раздел «Помощь» пока в разработке.", reply_markup=main_menu_kb())


@router.message(F.text == "🌐 Мои ресурсы")
async def resources(message: Message):
    await message.answer("Раздел «Ресурсы» пока в разработке.", reply_markup=main_menu_kb())


@router.message(F.text == "📦 Мои продукты")
async def products(message: Message):
    await message.answer("Выберите срок подписки", reply_markup=main_menu_kb())
    await message.answer("Тарифы:", reply_markup=plans_kb())


@router.callback_query(F.data.startswith("plan:"))
async def plan_selected(callback):
    plan_key = callback.data.split(":", 1)[1].strip()
    if plan_key not in PLANS:
        await callback.answer("Неизвестный тариф")
        return

    plan = PLANS[plan_key]
    user = callback.from_user

    tally_url = build_tally_url(
        TALLY_FORM_URL,
        tg_id=user.id,
        tg_username=user.username or "",
        plan_key=plan_key,
    )

    text = (
        f"Для оплаты Вам необходимо перевести {plan['amount_usdt']} USDT:\n\n"
        f"{USDT_TRC20_ADDRESS} (USDT, сеть TRC20)\n\n"
        f"После оплаты нажмите «Подтверждение оплаты» и заполните форму."
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подтверждение оплаты", web_app=WebAppInfo(url=tally_url))],
        [InlineKeyboardButton(text="В главное меню", callback_data="close")],
    ])

    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "close")
async def close_cb(callback):
    await callback.message.answer("Главное меню", reply_markup=main_menu_kb())
    await callback.answer()


@router.message(F.text == "👤 Личный кабинет")
async def cabinet(message: Message, notion: NotionClient):
    data = await resolve_cabinet(notion, str(message.from_user.id))

    discord = data["discord"] if data["discord"] else "Не указан"
    email = data["email"] if data["email"] else "Не указан"

    if data["status"] == "active":
        exp = data["expires_at"].strftime("%d.%m.%Y")
        status_line = f"Hadiukov Community — до {exp}"
    elif data["status"] == "pending":
        status_line = "Заявка на проверке"
    else:
        status_line = "Нет активной подписки"

    text = (
        "👤 Личный кабинет\n\n"
        f"Discord: {discord}\n"
        f"Email: {email}\n\n"
        f"Статус: {status_line}"
    )

    await message.answer(text, reply_markup=main_menu_kb())


# ---------------------------
# App bootstrap
# ---------------------------

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is empty. Set env BOT_TOKEN.")

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    notion = NotionClient(NOTION_TOKEN, NOTION_DATABASE_ID)

    # прокинем notion в хэндлеры
    dp["notion"] = notion

    # dependency injection via middleware-like simple getter
    @dp.message.middleware()
    async def inject_notion(handler, event, data):
        data["notion"] = notion
        return await handler(event, data)

    @dp.callback_query.middleware()
    async def inject_notion_cb(handler, event, data):
        data["notion"] = notion
        return await handler(event, data)

    try:
        await dp.start_polling(bot)
    finally:
        await notion.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
