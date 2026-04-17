import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Переменная окружения {name} не задана. "
            f"Проверь, что .env существует в {Path(__file__).parent} и содержит {name}=..."
        )
    return value


# Telegram
BOT_TOKEN = _require("REESTR_BOT_TOKEN")
ALLOWED_USERS = [int(x) for x in os.getenv("ALLOWED_USERS", "").split(",") if x]

# Claude API
CLAUDE_API_KEY = _require("CLAUDE_API_KEY")
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# SberCRM
SBERCRM_EMAIL = _require("SBERCRM_EMAIL")
SBERCRM_PASSWORD = _require("SBERCRM_PASSWORD")

# Пути
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "reestr_template.xlsx")
TEMP_DIR = os.getenv("TEMP_DIR", "/tmp/reestr-bot")
