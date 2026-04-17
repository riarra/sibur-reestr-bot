"""Клиент SberCRM API — поиск сделок, скачивание CMR и ShipmentDetails."""

import asyncio
import logging
import time
from urllib.parse import quote

import aiohttp

from config import SBERCRM_EMAIL, SBERCRM_PASSWORD

log = logging.getLogger(__name__)

SBERCRM_BASE = "https://app.sbercrm.com/react-gateway/api"
DEALS_ENTITY = "83343b17-cb33-11ee-84ee-a53aec1dc8d6"
MAX_PDF_SIZE = 20 * 1024 * 1024
MAX_RETRIES = 3
RETRY_DELAY = 2

# Папки с CMR в сделке
CMR_FOLDERS = {"Оригинал СМР", "ФОТО СМР", "CMR"}
# Папки с заявками (ShipmentDetails)
SHIPMENT_FOLDERS = {"Заявка", "Заявки", "ShipmentDetails", "Документы перевозки", "Юридический отдел"}


def _extract_sf_number(raw: str) -> int:
    """Извлекает числовой номер СФ из формата CRM '0000-0000746' → 746."""
    import re
    if not raw:
        return 0
    m = re.search(r'(\d+)\s*$', raw.replace("-", ""))
    if m:
        return int(m.group(1))
    return 0


def _parse_vehicle_trailer(raw: str) -> tuple[str, str]:
    """Парсит 'MAN TGX 80T707AB  / KRONE  800639BA' → ('80T707AB', '800639BA')."""
    import re
    numbers = re.findall(r'\b([A-Z0-9]{6,})\b', raw.upper().replace(" ", ""))
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    elif len(numbers) == 1:
        return numbers[0], ""
    if "/" in raw:
        parts = raw.split("/")
        left = parts[0].strip().split()
        right = parts[1].strip().split()
        v = left[-1] if left else ""
        t = right[-1] if right else ""
        return v, t
    return "", ""


class SberCRMClient:
    """Авто-логин и работа с SberCRM API."""

    CACHE_TTL = 600  # 10 минут

    def __init__(self):
        self.email = SBERCRM_EMAIL
        self._password = SBERCRM_PASSWORD
        self._token: str | None = None
        self._token_time: float = 0
        self._session: aiohttp.ClientSession | None = None
        self._deals_cache: list[dict] = []
        self._cache_time: float = 0

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        if not self._token or (time.time() - self._token_time) > 36000:
            await self._login()
        return self._session

    async def _login(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        async with self._session.post(
            f"{SBERCRM_BASE}/auth/login",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=f"username={self.email}&password={self._password}",
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(f"SberCRM login failed: HTTP {resp.status}")
            data = await resp.json()
            self._token = data["access_token"]
            self._token_time = time.time()
            log.info("SberCRM: авторизован как %s", self.email)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _api_request(self, method: str, url: str, **kwargs):
        session = await self._ensure_session()
        for attempt in range(MAX_RETRIES):
            try:
                async with session.request(method, url, headers=self._headers(), **kwargs) as resp:
                    if resp.status == 401:
                        self._token = None
                        session = await self._ensure_session()
                        continue
                    if resp.status >= 500:
                        await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                        continue
                    resp.raise_for_status()
                    return await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == MAX_RETRIES - 1:
                    raise
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
        raise RuntimeError(f"Не удалось выполнить запрос: {url}")

    async def find_deal_by_sf_and_vehicle(self, sf_number: int, vehicle_number: str) -> dict | None:
        """Найти сделку по двум триггерам: номер СФ + номер ТС."""
        norm_vehicle = vehicle_number.strip().upper().replace(" ", "")
        sf_str = str(sf_number)

        # Пробуем серверный фильтр по номеру ТС
        try:
            data = await self._api_request(
                "POST",
                f"{SBERCRM_BASE}/core/data/{DEALS_ENTITY}/list",
                json={
                    "paging": {"pageNum": 0, "itemsPerPage": 50},
                    "sorting": [{"field": "createdDate", "order": "DESC"}],
                    "filter": {
                        "type": "AND",
                        "conditions": [{
                            "field": "nomer_mashiny$c",
                            "operator": "CONTAINS",
                            "value": norm_vehicle,
                        }],
                    },
                },
            )
        except Exception:
            log.warning("Серверный фильтр не сработал, фоллбек для ТС %s СФ %s", norm_vehicle, sf_str)
            return await self._find_deal_fallback(sf_number, norm_vehicle)

        # Сверяем по двум триггерам: ТС + номер СФ
        for deal in data.get("data", []):
            vehicle = (deal.get("nomer_mashiny$c") or "").strip().upper().replace(" ", "")
            raw_sf = deal.get("nomer_scheta_faktury$c") or ""
            deal_sf = _extract_sf_number(raw_sf)

            if (norm_vehicle in vehicle or vehicle in norm_vehicle) and deal_sf == sf_number:
                log.info("Совпадение: СФ %d + ТС %s → сделка %s", sf_number, norm_vehicle, deal.get("name"))
                return self._deal_to_dict(deal, norm_vehicle)

        # Если по двум не нашли — ищем только по ТС (совпадение может быть без СФ)
        for deal in data.get("data", []):
            vehicle = (deal.get("nomer_mashiny$c") or "").strip().upper().replace(" ", "")
            if norm_vehicle in vehicle or vehicle in norm_vehicle:
                log.warning("Совпадение только по ТС %s (СФ не совпал), сделка %s", norm_vehicle, deal.get("name"))
                return self._deal_to_dict(deal, norm_vehicle)

        return None

    async def _find_deal_fallback(self, sf_number: int, norm_vehicle: str) -> dict | None:
        """Фоллбек: постраничный поиск, сверка по СФ + ТС."""
        page = 0
        while page < 25:
            data = await self._api_request(
                "POST",
                f"{SBERCRM_BASE}/core/data/{DEALS_ENTITY}/list",
                json={
                    "paging": {"pageNum": page, "itemsPerPage": 200},
                    "sorting": [{"field": "createdDate", "order": "DESC"}],
                },
            )
            items = data.get("data", [])
            if not items:
                break

            for deal in items:
                vehicle = (deal.get("nomer_mashiny$c") or "").strip().upper().replace(" ", "")
                raw_sf = deal.get("nomer_scheta_faktury$c") or ""
                deal_sf = _extract_sf_number(raw_sf)

                if (norm_vehicle in vehicle or vehicle in norm_vehicle) and deal_sf == sf_number:
                    return self._deal_to_dict(deal, norm_vehicle)

            page += 1

        return None

    def _deal_to_dict(self, deal: dict, matched_vehicle: str) -> dict:
        """Преобразует сделку CRM в словарь со всеми данными для реестра."""
        raw_sf = deal.get("nomer_scheta_faktury$c") or ""
        sf_number = _extract_sf_number(raw_sf)

        # Парсим направление из name: "06927 - Нижнекамск - Ангрен"
        direction = deal.get("napravlenie$c") or ""
        loading_city = ""
        unloading_city = ""
        if " - " in direction:
            parts = direction.split(" - ", 1)
            loading_city = parts[0].strip()
            unloading_city = parts[1].strip()

        # Парсим ТС и прицеп из "MAN TGX 80T707AB  / KRONE  800639BA"
        raw_vehicle = deal.get("nomer_mashiny$c") or ""
        vehicle_num, trailer_num = _parse_vehicle_trailer(raw_vehicle)

        # Регион из подразделения: "Узбекистан ПАО СИБУР ХОЛДИНГ" → "Узбекистан"
        dept_name = (deal.get("podrazdelenie_logist$c") or {}).get("name", "")
        region = dept_name.split(" ПАО")[0].split(" пао")[0] if dept_name else ""

        return {
            "id": deal["id"],
            "name": deal.get("name", ""),
            "vehicle": raw_vehicle,
            "matched_target": matched_vehicle,
            # Данные для реестра
            "sf_number": sf_number,
            "sf_date": deal.get("data_vygruzki_1$c", ""),
            "product_name": deal.get("naimenovanie_produkczii$c") or "",
            "loading_city": loading_city,
            "unloading_city": unloading_city,
            "region": region,
            "sender_name": (deal.get("organization") or {}).get("name", ""),
            "receiver_name": "",  # грузополучатель — нет в полях сделки
            "vehicle_number": vehicle_num,
            "trailer_number": trailer_num,
            "weight": deal.get("ves_sdelka$c") or 0,
            "rate_usd": deal.get("czena_prodazhi$c") or 0,
            "loading_date": deal.get("data_zagruzki_calculate$c") or "",
            "unloading_date": deal.get("data_obnovleniya_calc$c") or "",
            "department": dept_name,
        }


    async def find_deals(self, records: list) -> list[dict]:
        """Найти сделки по списку записей (СФ + ТС).

        Загружает все сделки один раз, потом сверяет локально.
        """
        # Собираем целевые пары (sf, vehicle)
        targets = []
        for rec in records:
            sf = getattr(rec, "invoice_number", 0) or getattr(rec, "act_number", 0)
            vn = (getattr(rec, "vehicle_number", "") or "").strip().upper().replace(" ", "")
            targets.append((sf, vn))

        # Загружаем все сделки один раз
        all_deals = await self._load_all_deals()
        log.info("Загружено %d сделок из CRM", len(all_deals))

        results = []
        for sf, vn in targets:
            found = None
            for deal in all_deals:
                vehicle = (deal.get("nomer_mashiny$c") or "").strip().upper().replace(" ", "")
                raw_sf = deal.get("nomer_scheta_faktury$c") or ""
                deal_sf = _extract_sf_number(raw_sf)

                vehicle_match = vn and (vn in vehicle or vehicle in vn)
                sf_match = sf and deal_sf == sf

                # Сверка по двум триггерам
                if vehicle_match and sf_match:
                    found = self._deal_to_dict(deal, vn)
                    break

            # Фоллбек — только по ТС
            if not found:
                for deal in all_deals:
                    vehicle = (deal.get("nomer_mashiny$c") or "").strip().upper().replace(" ", "")
                    if vn and (vn in vehicle or vehicle in vn):
                        found = self._deal_to_dict(deal, vn)
                        log.warning("Совпадение только по ТС %s (СФ %d не совпал)", vn, sf)
                        break

            if found:
                results.append(found)
            else:
                log.warning("Сделка не найдена: СФ %s, ТС %s", sf, vn)

        log.info("Найдено %d сделок из %d записей", len(results), len(targets))
        return results

    async def _load_all_deals(self) -> list[dict]:
        """Загружает все сделки из CRM (постранично). Кеш 10 мин."""
        if self._deals_cache and (time.time() - self._cache_time) < self.CACHE_TTL:
            log.info("CRM: используем кеш (%d сделок)", len(self._deals_cache))
            return self._deals_cache

        all_deals = []
        page = 0
        while True:
            data = await self._api_request(
                "POST",
                f"{SBERCRM_BASE}/core/data/{DEALS_ENTITY}/list",
                json={
                    "paging": {"pageNum": page, "itemsPerPage": 500},
                    "sorting": [{"field": "createdDate", "order": "DESC"}],
                },
            )
            items = data.get("data", [])
            if not items:
                break
            all_deals.extend(items)
            if len(items) < 500:
                break
            page += 1

        self._deals_cache = all_deals
        self._cache_time = time.time()
        log.info("CRM: загружено %d сделок, кеш обновлён", len(all_deals))
        return all_deals

    async def get_deal_files(self, deal_id: str) -> dict:
        """Получить CMR и ShipmentDetails файлы из сделки."""
        root = await self._api_request(
            "GET",
            f"{SBERCRM_BASE}/file-storage/v1/folder?path=/{DEALS_ENTITY}/{deal_id}",
        )

        cmr_files = []
        shipment_files = []

        # Собираем задачи по папкам
        for folder in root.get("folders", []):
            folder_name = folder.get("tags", {}).get("folderName", [""])[0]
            path = folder["path"]

            is_cmr = any(t.lower() in folder_name.lower() for t in CMR_FOLDERS)
            is_shipment = any(t.lower() in folder_name.lower() for t in SHIPMENT_FOLDERS)

            if is_cmr or is_shipment:
                files = await self._list_folder_files(path)
                if is_cmr:
                    cmr_files.extend(files)
                if is_shipment:
                    shipment_files.extend(files)

        # Если ShipmentDetails не нашли в специальных папках — ищем .docx в корне
        if not shipment_files:
            for f in root.get("files", []):
                name = f.get("fileName", "")
                if name.lower().endswith(".docx") and "shipment" in name.lower():
                    shipment_files.append({
                        "name": name, "key": f["key"],
                        "size": f.get("size", 0), "type": f.get("type", ""),
                    })

        return {"cmr": cmr_files, "shipments": shipment_files}

    async def _list_folder_files(self, path: str) -> list[dict]:
        data = await self._api_request(
            "GET", f"{SBERCRM_BASE}/file-storage/v1/folder?path={path}"
        )
        result = []
        for f in data.get("files", []):
            if f.get("size", 0) <= MAX_PDF_SIZE:
                result.append({
                    "name": f["fileName"], "key": f["key"],
                    "size": f.get("size", 0), "type": f.get("type", ""),
                })
        return result

    async def download_file(self, file_key: str) -> bytes:
        """Скачать файл из CRM."""
        session = await self._ensure_session()
        encoded_key = quote(file_key, safe="/")
        for attempt in range(MAX_RETRIES):
            try:
                async with session.get(
                    f"{SBERCRM_BASE}/file-storage/v1/file/download?key={encoded_key}",
                    headers=self._headers(),
                ) as resp:
                    if resp.status == 401:
                        self._token = None
                        session = await self._ensure_session()
                        continue
                    resp.raise_for_status()
                    return await resp.read()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == MAX_RETRIES - 1:
                    raise
                await asyncio.sleep(RETRY_DELAY)
        raise RuntimeError("Не удалось скачать файл")
