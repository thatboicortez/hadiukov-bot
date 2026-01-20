import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TALLY_FORM_URL = os.getenv("TALLY_FORM_URL", "https://tally.so/r/jao451").strip()

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "").strip()
