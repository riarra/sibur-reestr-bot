"""Извлечение номера CMR из PDF/фото — сначала текстовый парсинг, фоллбек на Claude API."""

import base64
import json
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class CMRRecord:
    cmr_number: str
    vehicle_number: str
    trailer_number: str


def _extract_cmr_from_text(text: str) -> CMRRecord | None:
    """Извлекает номер CMR из текста PDF (бесплатно, без API)."""
    if not text or len(text) < 10:
        return None

    cmr_number = ""
    # Ищем "CMR №" или "CMR N" или "CMR No" + число
    m = re.search(r'CMR\s*[№N][oо]?\s*[:\s]*(\d{4,})', text, re.IGNORECASE)
    if m:
        cmr_number = m.group(1)
    else:
        # Ищем просто длинное число рядом с CMR
        m = re.search(r'CMR.*?(\d{6,})', text, re.IGNORECASE)
        if m:
            cmr_number = m.group(1)

    if not cmr_number:
        return None

    log.info(f"CMR {cmr_number} извлечён из текста PDF (без API)")
    return CMRRecord(cmr_number=cmr_number, vehicle_number="", trailer_number="")


def _extract_pdf_text(pdf_path: Path) -> str:
    """Извлекает текст из PDF через pdftotext."""
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def _pdf_to_image(pdf_path: Path) -> Path:
    """Конвертирует первую страницу PDF в PNG."""
    output_dir = tempfile.mkdtemp()
    output_prefix = str(Path(output_dir) / "page")
    subprocess.run(
        ["pdftoppm", "-png", "-r", "200", "-f", "1", "-l", "1", str(pdf_path), output_prefix],
        check=True, capture_output=True,
    )
    result = Path(output_dir) / "page-1.png"
    if not result.exists():
        for f in Path(output_dir).glob("*.png"):
            return f
    return result


async def recognize_cmr(file_path: str | Path) -> CMRRecord:
    """Извлекает номер CMR. Сначала текстовый парсинг, потом Claude API."""
    file_path = Path(file_path)

    # 1. Пробуем извлечь из текста PDF (бесплатно)
    if file_path.suffix.lower() == ".pdf":
        text = _extract_pdf_text(file_path)
        record = _extract_cmr_from_text(text)
        if record and record.cmr_number:
            return record

    # 2. Фоллбек — Claude API (для сканов/фото)
    log.info(f"Текстовый парсинг не дал результат, используем Claude API для {file_path.name}")
    return await _recognize_cmr_claude(file_path)


async def _recognize_cmr_claude(file_path: Path) -> CMRRecord:
    """OCR через Claude API (фоллбек)."""
    import anthropic
    from config import CLAUDE_API_KEY, CLAUDE_MODEL

    PROMPT = """Ты видишь скан международной товарно-транспортной накладной CMR.

Извлеки:
1. cmr_number — номер CMR (в правом верхнем углу, поле "CMR №" или "CMR N")
2. vehicle_number — гос. номер ТС из поля 25 (первая часть до /)
3. trailer_number — номер прицепа из поля 25 (вторая часть после /)

Верни ТОЛЬКО JSON объект, без markdown:
{"cmr_number": "2329314", "vehicle_number": "10E551GB", "trailer_number": "105220BA"}"""

    if file_path.suffix.lower() == ".pdf":
        image_path = _pdf_to_image(file_path)
    else:
        image_path = file_path

    with open(image_path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    media_type = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    }.get(image_path.suffix.lower(), "image/png")

    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]

    r = json.loads(text)
    result = CMRRecord(
        cmr_number=str(r.get("cmr_number", "")).strip(),
        vehicle_number=str(r.get("vehicle_number", "")).strip(),
        trailer_number=str(r.get("trailer_number", "")).strip(),
    )

    log.info(f"CMR {result.cmr_number} → ТС {result.vehicle_number} из {file_path.name} (Claude API)")
    return result
