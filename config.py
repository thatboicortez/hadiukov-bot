import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _get_env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


BOT_TOKEN = _get_env("BOT_TOKEN")
TALLY_FORM_URL = _get_env("TALLY_FORM_URL")

# Notion (бот только читает)
NOTION_TOKEN = _get_env("NOTION_TOKEN")
NOTION_DATABASE_ID = _get_env("NOTION_DATABASE_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is empty. Set env BOT_TOKEN.")
if not TALLY_FORM_URL:
    raise RuntimeError("TALLY_FORM_URL is empty. Set env TALLY_FORM_URL.")
if not NOTION_TOKEN:
    raise RuntimeError("NOTION_TOKEN is empty. Set env NOTION_TOKEN.")
if not NOTION_DATABASE_ID:
    raise RuntimeError("NOTION_DATABASE_ID is empty. Set env NOTION_DATABASE_ID.")
