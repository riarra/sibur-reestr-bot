"""OCR скриншотов таблицы УПД через Claude API."""

import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import anthropic

from config import CLAUDE_API_KEY, CLAUDE_MODEL

log = logging.getLogger(__name__)

PROMPT = """Ты видишь скриншот таблицы из CRM-системы с данными УПД (универсальных передаточных документов).

Извлеки из таблицы ВСЕ строки. Для каждой строки верни:
- act_number: номер Акта выполненных работ (целое число)
- invoice_number: номер Счета-фактуры / Инвойса (целое число)
- act_date: дата Акта/СФ (формат ДД.ММ.ГГГГ)
- vehicle_number: гос. номер ТС (например 10E551GB, 80M185VA)
- trailer_number: гос. номер прицепа (например 105220BA, 808599AA)
- rate_usd: ставка в USD (целое число), если есть

Верни JSON массив объектов. ТОЛЬКО JSON, без markdown и пояснений.

Пример ответа:
[{"act_number": 651, "invoice_number": 746, "act_date": "23.03.2026", "vehicle_number": "80D830RA", "trailer_number": "806813AA", "rate_usd": 3900}]"""


@dataclass
class UPDRecord:
    upd_number: int
    vehicle_number: str
    trailer_number: str
    rate_usd: float = 0.0
    act_number: int = 0        # Номер Акта выполненных работ
    invoice_number: int = 0    # Номер Счета-фактуры (Инвойса)
    act_date: str = ""         # Дата Акта / СФ (формат ДД.ММ.ГГГГ)


async def recognize_upd_screenshot(image_path: str | Path) -> list[UPDRecord]:
    """Распознаёт скриншот таблицы УПД через Claude API."""
    image_path = Path(image_path)
    with open(image_path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    suffix = image_path.suffix.lower()
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix, "image/png")

    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    )

    text = response.content[0].text.strip()
    # Убираем markdown обёртку если есть
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]

    records = json.loads(text)
    result = []
    for r in records:
        act_num = int(r.get("act_number", 0))
        inv_num = int(r.get("invoice_number", 0))
        result.append(UPDRecord(
            upd_number=act_num or inv_num,  # для обратной совместимости
            vehicle_number=str(r.get("vehicle_number", "")).strip(),
            trailer_number=str(r.get("trailer_number", "")).strip(),
            rate_usd=float(r.get("rate_usd", 0)),
            act_number=act_num,
            invoice_number=inv_num,
            act_date=str(r.get("act_date", "")).strip(),
        ))

    log.info(f"Распознано {len(result)} записей УПД из {image_path.name}")
    return result
