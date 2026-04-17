"""Точка входа — Telegram-бот для генерации реестра СИБУР."""

import asyncio
import logging
import logging.handlers
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, TEMP_DIR
from handlers.reestr import router as reestr_router

# Логирование: stdout + ротируемый файл
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DIR = os.path.join(TEMP_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "reestr-bot.log"),
    maxBytes=5 * 1024 * 1024,  # 5 MB
    backupCount=3,
    encoding="utf-8",
)
handlers.append(file_handler)

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=handlers)
log = logging.getLogger(__name__)


async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(reestr_router)

    log.info("Reestr Bot запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
