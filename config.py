import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Notion
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "").strip()
NOTION_VERSION = "2022-06-28"

# Tally base (mini app). Можно не хранить в секретах — есть дефолт.
TALLY_FORM_URL = os.getenv("TALLY_FORM_URL", "https://tally.so/r/jao451").strip()

# Product / prices
PRODUCT_NAME = "Hadiukov Community"

# Адрес для оплаты (замени на свой, если нужно)
USDT_TRC20_ADDRESS = "TAzH2VDmTZnmAjgwDUUVDDFGntpWk7a5kQ"

# Прайс-лист
PLANS = {
    "1m": {
        "label": "1 месяц — 50 USDT",
        "amount_usdt": "50",
        "amount_uah": "2200",
        "months": 1,
    },
    "3m": {
        "label": "3 месяца — 120 USDT",
        "amount_usdt": "120",
        "amount_uah": "5200",
        "months": 3,
    },
}

PAY_METHOD_DEFAULT = "Crypto (USDT)"
