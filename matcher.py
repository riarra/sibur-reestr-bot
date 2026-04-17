"""Сопоставление данных из УПД, ShipmentDetails и CMR по номеру ТС."""

import logging
from dataclasses import dataclass, field

from parsers.shipment import (
    ShipmentData, get_loading_city, get_unloading_city,
    get_country_name, get_weight_float, get_rate_float,
)
from parsers.upd_ocr import UPDRecord
from parsers.cmr_ocr import CMRRecord

log = logging.getLogger(__name__)


@dataclass
class ReestrRow:
    """Одна строка реестра — объединённые данные из всех источников."""
    # Из УПД
    upd_number: int = 0
    rate_usd: float = 0.0
    act_number: int = 0        # Номер Акта выполненных работ
    invoice_number: int = 0    # Номер Счета-фактуры (Инвойса)
    act_date: str = ""         # Дата Акта / СФ

    # Из ShipmentDetails
    transport_number: str = ""
    vehicle_number: str = ""
    trailer_number: str = ""
    product_name: str = ""
    contract: str = ""
    sender_name: str = ""      # грузоотправитель
    receiver_name: str = ""    # грузополучатель
    loading_city: str = ""
    unloading_city: str = ""
    country: str = ""
    loading_date: str = ""
    unloading_date: str = ""
    weight: float = 0.0

    # Из CMR
    cmr_number: str = ""

    # Флаги
    has_upd: bool = False
    has_shipment: bool = False
    has_cmr: bool = False


def _normalize_vehicle(v: str) -> str:
    """Нормализует номер ТС для сравнения."""
    return v.strip().upper().replace(" ", "").replace("-", "")


def match_data(
    upd_records: list[UPDRecord],
    shipments: list[ShipmentData],
    cmr_records: list[CMRRecord],
    deal_map: dict[str, dict] | None = None,
) -> list[ReestrRow]:
    """Сопоставляет данные из трёх источников.

    Если deal_map передан — привязка через CRM-сделку (надёжно).
    Иначе — фоллбек по номеру ТС из документов.
    """

    # Фоллбек-индексы (если deal_map не передан)
    shipment_by_vehicle: dict[str, ShipmentData] = {}
    cmr_by_vehicle: dict[str, CMRRecord] = {}
    if not deal_map:
        for s in shipments:
            key = _normalize_vehicle(s.vehicle_number)
            if key:
                shipment_by_vehicle[key] = s
        for c in cmr_records:
            key = _normalize_vehicle(c.vehicle_number)
            if key:
                cmr_by_vehicle[key] = c

    rows: list[ReestrRow] = []

    for upd in upd_records:
        row = ReestrRow(
            upd_number=upd.upd_number,
            act_number=upd.act_number,
            invoice_number=upd.invoice_number,
            act_date=upd.act_date,
            has_upd=True,
        )
        vkey = _normalize_vehicle(upd.vehicle_number)

        # Получаем ShipmentDetails и CMR
        if deal_map and vkey in deal_map:
            sd = deal_map[vkey].get("shipment")
            cmr = deal_map[vkey].get("cmr")
            # Дата СФ из CRM
            if not row.act_date:
                row.act_date = deal_map[vkey].get("sf_date", "")
        else:
            sd = shipment_by_vehicle.get(vkey)
            cmr = cmr_by_vehicle.get(vkey)

        # Заполняем из ShipmentDetails (заявка)
        if sd:
            row.has_shipment = True
            row.transport_number = sd.transport_number
            row.vehicle_number = sd.vehicle_number or upd.vehicle_number
            row.trailer_number = sd.trailer_number or upd.trailer_number
            row.product_name = sd.product_name
            row.contract = sd.contract
            row.sender_name = sd.planning_dept
            row.receiver_name = sd.client_name
            row.loading_city = get_loading_city(sd)
            row.unloading_city = get_unloading_city(sd)
            row.country = get_country_name(sd)
            row.loading_date = sd.loading_date
            row.unloading_date = sd.unloading_date
            row.weight = get_weight_float(sd)
            row.rate_usd = upd.rate_usd if upd.rate_usd > 0 else get_rate_float(sd)
        else:
            row.vehicle_number = upd.vehicle_number
            row.trailer_number = upd.trailer_number
            row.rate_usd = upd.rate_usd
            log.warning(f"Акт {upd.act_number}: ShipmentDetails не найден для ТС {upd.vehicle_number}")

        # Заполняем из CMR
        if cmr:
            row.has_cmr = True
            row.cmr_number = cmr.cmr_number
        else:
            log.warning(f"Акт {upd.act_number}: CMR не найден для ТС {upd.vehicle_number}")

        rows.append(row)

    matched = sum(1 for r in rows if r.has_shipment and r.has_cmr)
    log.info(f"Сопоставлено: {matched}/{len(rows)} полных строк")

    return rows
