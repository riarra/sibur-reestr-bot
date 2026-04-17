# sibur-reestr-bot

Telegram-бот для генерации Excel-реестра отгрузок СИБУР из документов CMR/УПД.

## Стек
- `aiogram 3.x` — Telegram
- `anthropic` (Claude Sonnet 4) — извлечение данных из документов
- `openpyxl` — генерация Excel из шаблона
- `pdf2image` — конвертация PDF → изображения для OCR
- `python-docx`, `aiohttp`, `aiofiles`

## Карта "задача → файл" (читать ТОЛЬКО нужное)

| Тип задачи | Файл |
|-----------|------|
| FSM-флоу / команды Telegram | `handlers/reestr.py` (14KB) |
| OCR накладной CMR | `parsers/cmr_ocr.py` |
| OCR УПД | `parsers/upd_ocr.py` |
| Сборка данных отгрузки | `parsers/shipment.py` |
| Генерация Excel из шаблона | `generator.py` |
| Matching данных | `matcher.py` |
| Запросы к SberCRM | `crm_client.py` (16KB — Grep перед Read) |
| Конфиг / токены / пути | `config.py` |
| Точка входа / логирование | `bot.py` |
| Шаблон Excel (бинарь) | `templates/reestr_template.xlsx` |

**Правило:** перед чтением кода — найди задачу в таблице. Если задача про OCR — НЕ открывай `crm_client.py` и `generator.py`.

## Запуск
```bash
cd "/Users/vadim/Desktop/IT проекты/sibur-reestr-bot"
pip install -r requirements.txt
python bot.py
```

## Окружение
- `REESTR_BOT_TOKEN` — Telegram bot
- `CLAUDE_API_KEY` — Anthropic
- `SBERCRM_EMAIL`, `SBERCRM_PASSWORD` — SberCRM
- `ALLOWED_USERS` — список Telegram user IDs через запятую
- `TEMP_DIR` — рабочая папка (default `/tmp/reestr-bot`)

## Логи
`$TEMP_DIR/logs/reestr-bot.log` — ротация 5MB × 3 файла.

## Технический долг
- Токены в `config.py` заданы как fallback в коде — при правках убирать из дефолтов.
- Нет тестов.
- `MemoryStorage` — состояние теряется при рестарте.
