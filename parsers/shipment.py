"""Парсер ShipmentDetails.docx — извлечение данных перевозки из СИБУР-портала."""

import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ShipmentData:
    transport_number: str = ""       # № перевозки
    vehicle_number: str = ""         # Гос. номер ТС
    trailer_number: str = ""         # Гос. номер прицепа
    product_name: str = ""           # Название продукта
    contract: str = ""               # Договор
    client_name: str = ""            # Наименование клиента (грузополучатель)
    planning_dept: str = ""          # Отдел планирования (грузоотправитель)
    loading_date: str = ""           # Дата погрузки
    unloading_date: str = ""         # Дата выгрузки
    loading_place: str = ""          # Место загрузки
    unloading_place: str = ""        # Место разгрузки
    weight: str = ""                 # Вес
    rate_usd: str = ""               # Расчетная цена
    country: str = ""                # Страна покупателя
    region: str = ""                 # Регион


def _extract_city(place: str) -> str:
    """Извлекает название города (поддерживает многословные: 'Нижний Новгород')."""
    if not place:
        return ""

    # 'г. Нижний Новгород' / 'г Набережные Челны'
    m = re.search(
        r'г\.?\s+([А-ЯЁ][А-ЯЁа-яё\-]*(?:\s+[А-ЯЁ][А-ЯЁа-яё\-]*)*)',
        place,
    )
    if m:
        return m.group(1).strip()

    # 'р-н Название (составное)'
    m = re.search(
        r'р-н\s+([А-ЯЁ][А-ЯЁа-яё\-]*(?:\s+[А-ЯЁ][А-ЯЁа-яё\-]*)*)',
        place,
    )
    if m:
        return m.group(1).strip()

    # 'с Название' (село) — обычно одно слово, но допускаем составное
    m = re.search(
        r'\bс\s+([А-ЯЁ][А-ЯЁа-яё\-]*(?:\s+[А-ЯЁ][А-ЯЁа-яё\-]*)*)',
        place,
    )
    if m:
        return m.group(1).strip()

    # Фоллбек: убираем код отдела ('9W22, ...') и берём первый разумный сегмент
    cleaned = re.sub(r'^[0-9A-Z]+,?\s*', '', place).strip()
    for word in cleaned.split(","):
        word = word.strip()
        m = re.match(r'([А-ЯЁA-Z][а-яёa-z\-]+(?:\s+[А-ЯЁA-Z][а-яёa-z\-]+)*)', word)
        if m and m.group(1).lower() not in ("обл", "тер", "влд"):
            return m.group(1)
    return place


def _extract_dept_name(dept: str) -> str:
    """Извлекает читаемое название грузоотправителя."""
    mapping = {
        "9121": "ЗапСибНефтехим (Тобольск)",
        "9011": "ТомскНефтеХим (Томск)",
        "9W22": "КСС РУС (Ворсино)",
        "9W31": "ЮЛК Тобольск",
        "9351": "Нижнекамскнефтехим",
        "9131": "КАЗАНЬОРГСИНТЕЗ",
    }
    for code, name in mapping.items():
        if code in dept:
            return name
    # Убираем числовой код из начала: "9351 Нижнекамскнефтехим" → "Нижнекамскнефтехим"
    stripped = re.sub(r'^[0-9A-Z]+\s+', '', dept)
    return stripped if stripped else dept


def parse_shipment_docx(file_path: str | Path) -> ShipmentData:
    """Парсит ShipmentDetails.docx и возвращает структурированные данные."""
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    lines: list[str] = []

    with zipfile.ZipFile(file_path) as z:
        tree = ET.parse(z.open('word/document.xml'))
        for p in tree.findall('.//w:p', ns):
            txt = ''.join(r.text or '' for r in p.findall('.//w:t', ns))
            if txt.strip():
                lines.append(txt.strip())

    data = ShipmentData()

    # Парсим пары ключ-значение (формат: "Ключ\tЗначение" или последовательные строки)
    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("№ перевозки") and not data.transport_number:
            # Может быть "№ перевозки\t2207007" или следующая строка
            parts = line.split("\t")
            if len(parts) > 1 and parts[1].strip().isdigit():
                data.transport_number = parts[1].strip()
            elif i + 1 < len(lines) and lines[i + 1].strip().isdigit():
                data.transport_number = lines[i + 1].strip()

        elif line.startswith("Гос. номер") and "прицеп" not in line.lower() and not data.vehicle_number:
            if i + 1 < len(lines):
                data.vehicle_number = lines[i + 1].strip()

        elif "Гос. номер прицепа" in line and not data.trailer_number:
            if i + 1 < len(lines):
                data.trailer_number = lines[i + 1].strip()

        elif line.startswith("Название продукта") and not data.product_name:
            if i + 1 < len(lines):
                data.product_name = lines[i + 1].strip()

        elif line.startswith("Договор") and not data.contract:
            if i + 1 < len(lines):
                data.contract = lines[i + 1].strip()

        elif line.startswith("Наименование клиента") and not data.client_name:
            if i + 1 < len(lines):
                data.client_name = lines[i + 1].strip()

        elif line.startswith("Отдел планирования") and not data.planning_dept:
            if i + 1 < len(lines):
                data.planning_dept = _extract_dept_name(lines[i + 1].strip())

        elif line.startswith("Дата погрузки") and not data.loading_date:
            if i + 1 < len(lines):
                data.loading_date = lines[i + 1].strip()

        elif line.startswith("Дата выгрузки") and not data.unloading_date:
            if i + 1 < len(lines):
                data.unloading_date = lines[i + 1].strip()

        elif line.startswith("Место загрузки") and not data.loading_place:
            if i + 1 < len(lines):
                data.loading_place = lines[i + 1].strip()

        elif line.startswith("Место разгрузки") and not data.unloading_place:
            if i + 1 < len(lines):
                data.unloading_place = lines[i + 1].strip()

        elif line.startswith("Вес") and not data.weight:
            if i + 1 < len(lines):
                data.weight = lines[i + 1].strip()

        elif line.startswith("Расчетная цена") and not data.rate_usd:
            if i + 1 < len(lines):
                data.rate_usd = lines[i + 1].strip()

        elif line.startswith("Страна покупателя") and not data.country:
            if i + 1 < len(lines):
                data.country = lines[i + 1].strip()

        elif line.startswith("Регион") and not data.region:
            if i + 1 < len(lines):
                data.region = lines[i + 1].strip()

        i += 1

    return data


def get_loading_city(data: ShipmentData) -> str:
    """Возвращает город погрузки."""
    return _extract_city(data.loading_place)


def get_unloading_city(data: ShipmentData) -> str:
    """Возвращает город разгрузки."""
    return _extract_city(data.unloading_place)


def get_country_name(data: ShipmentData) -> str:
    """Возвращает название страны."""
    mapping = {"UZ": "Узбекистан", "TJ": "Таджикистан", "AZ": "Азербайджан", "KZ": "Казахстан"}
    return mapping.get(data.country, data.country)


def get_weight_float(data: ShipmentData) -> float:
    """Извлекает вес как число."""
    m = re.search(r'([\d.,]+)', data.weight)
    return float(m.group(1).replace(',', '.')) if m else 0.0


def get_rate_float(data: ShipmentData) -> float:
    """Извлекает ставку как число."""
    m = re.search(r'([\d.,]+)', data.rate_usd)
    return float(m.group(1).replace(',', '.')) if m else 0.0
