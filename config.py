import os
from dotenv import load_dotenv


def _get_env(name: str, *, required: bool = True) -> str:
    """
    Read env var and optionally validate it's present.
    Keeps exact value except trimming surrounding whitespace (common .env issues).
    """
    value = os.getenv(name)
    if value is None:
        if required:
            raise RuntimeError(f"{name} is not set. Add it to environment/.env.")
        return ""
    value = value.strip()
    if required and not value:
        raise RuntimeError(f"{name} is empty. Set a non-empty value in environment/.env.")
    return value


# Load variables from .env if present (doesn't override already-set OS env vars)
load_dotenv(override=False)

BOT_TOKEN = _get_env("BOT_TOKEN")
NOTION_TOKEN = _get_env("NOTION_TOKEN")
NOTION_DATABASE_ID = _get_env("NOTION_DATABASE_ID")
TALLY_FORM_URL = _get_env("TALLY_FORM_URL")  # e.g. https://tally.so/r/jao451
