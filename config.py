import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Можно задать в Koyeb как env TALLY_FORM_URL,
# но если не задашь — возьмём хардкод отсюда.
TALLY_FORM_URL = os.getenv("TALLY_FORM_URL", "").strip() or "https://tally.so/r/ja0451"

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "").strip()

# (не обязательно) админ для текста приветствия
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@name").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is empty. Set env BOT_TOKEN.")
if not TALLY_FORM_URL:
    raise RuntimeError("TALLY_FORM_URL is empty. Set env TALLY_FORM_URL or hardcode it in config.py.")
if not NOTION_TOKEN:
    raise RuntimeError("NOTION_TOKEN is empty. Set env NOTION_TOKEN.")
if not NOTION_DATABASE_ID:
    raise RuntimeError("NOTION_DATABASE_ID is empty. Set env NOTION_DATABASE_ID.")
