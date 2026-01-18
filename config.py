import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

PRODUCT_NAME = "Sever by Hadiukov"
TALLY_FORM_URL = "https://tally.so/r/jao451"

# цены (как ты писал): 50 и 120
PRICES = {
    "1m": 50,
    "3m": 120,
}

PERIOD_TEXT = {
    "1m": "1 month",
    "3m": "3 months",
}

PERIOD_MONTHS = {
    "1m": 1,
    "3m": 3,
}