"""FSM-хендлер: ввод данных → поиск в CRM → парсинг → реестр."""

import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, FSInputFile

import re

from parsers.upd_ocr import recognize_upd_screenshot, UPDRecord
from parsers.shipment import parse_shipment_docx
from parsers.cmr_ocr import recognize_cmr
from crm_client import SberCRMClient
from matcher import match_data
from generator import generate_reestr, generate_summary
from config import TEMPLATE_PATH

log = logging.getLogger(__name__)
router = Router()

crm = SberCRMClient()


class ReestrStates(StatesGroup):
    waiting_input = State()  # Ждём скриншоты или текст с УПД/ТС


sessions: dict[int, dict] = {}


def _get_session(user_id: int) -> dict:
    if user_id not in sessions:
        sessions[user_id] = {"upd_records": []}
    return sessions[user_id]


def _validate_vehicle(v: str) -> bool:
    """Проверяет формат номера ТС (буквы+цифры, 6-10 символов)."""
    return bool(re.match(r'^[A-Za-z0-9]{6,10}$', v))


def _parse_text_input(text: str) -> tuple[list[UPDRecord], list[str]]:
    """Парсит текстовый ввод.

    Формат: СФ ТС
    Пример: 746 80D830RA

    СФ = номер счёт-фактуры (= номер акта). Бот сверяет с CRM.
    Возвращает (записи, ошибки).
    """
    records = []
    errors = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            errors.append(f"❌ `{line}` — нужно минимум 2 поля (СФ ТС)")
            continue
        try:
            sf_num = int(parts[0])
        except ValueError:
            errors.append(f"❌ `{parts[0]}` — СФ должен быть числом")
            continue

        vehicle = parts[1].upper()
        if not _validate_vehicle(vehicle):
            errors.append(f"⚠️ `{vehicle}` — подозрительный формат ТС")

        records.append(UPDRecord(
            upd_number=sf_num,
            vehicle_number=vehicle,
            trailer_number="",
            act_number=sf_num,
            invoice_number=sf_num,
        ))
    return records, errors


@router.message(Command("start", "reestr"))
async def cmd_reestr(message: Message, state: FSMContext):
    """Начинает новую сессию (по /start или /reestr)."""
    sessions[message.from_user.id] = {"upd_records": []}
    await state.set_state(ReestrStates.waiting_input)
    await message.answer(
        "🌹 *Розочка, привет!* Как дела? Хорошего тебе настроения, "
        "чтобы всё было и ничего за это не было 💐\n\n"
        "👋 *Бот реестра СИБУР готов к работе*\n\n"
        "📋 Отправь данные построчно в формате `СФ ТС`:\n"
        "```\n"
        "746 80D830RA\n"
        "727 4477QO02\n"
        "843 80T707AB\n"
        "```\n"
        "Можно сразу несколько строк одним сообщением.\n\n"
        "Бот сверит СФ + ТС с SberCRM, скачает заявки и CMR, "
        "распарсит их и сгенерирует готовый xlsx-реестр.\n\n"
        "*Команды:*\n"
        "/go — запустить генерацию\n"
        "/status — показать введённые записи\n"
        "/template — скачать пустой шаблон\n"
        "/cancel — отменить\n\n"
        "Жду данные ⤵️",
        parse_mode="Markdown",
    )


@router.message(ReestrStates.waiting_input, F.photo)
async def handle_photo(message: Message, state: FSMContext):
    """OCR скриншота таблицы УПД."""
    session = _get_session(message.from_user.id)
    await message.answer("⏳ Распознаю скриншот...")

    photo = message.photo[-1]
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    await message.bot.download(photo, tmp.name)

    try:
        records = await recognize_upd_screenshot(tmp.name)
        session["upd_records"].extend(records)

        text = f"✅ Распознано {len(records)} записей:\n"
        for r in records:
            text += f"\n`{r.upd_number}` | {r.vehicle_number} / {r.trailer_number}"
            if r.rate_usd:
                text += f" | {r.rate_usd:.0f}$"
        text += f"\n\n📊 Всего: {len(session['upd_records'])}. Ещё скриншоты или /go"
        await message.answer(text, parse_mode="Markdown")
    except Exception as e:
        log.exception("Ошибка OCR")
        await message.answer(f"❌ Ошибка распознавания: {e}")
    finally:
        os.unlink(tmp.name)


@router.message(ReestrStates.waiting_input, F.text, ~F.text.startswith("/"))
async def handle_text(message: Message, state: FSMContext):
    """Парсит текстовый ввод УПД."""
    session = _get_session(message.from_user.id)
    records, errors = _parse_text_input(message.text)

    if not records and not errors:
        await message.answer(
            "⚠️ Не удалось распознать. Формат:\n"
            "`СФ  ТС`\n"
            "Например: `746 80D830RA`",
            parse_mode="Markdown",
        )
        return

    # Проверка дублей
    existing_vehicles = {r.vehicle_number.upper() for r in session["upd_records"]}
    dupes = []
    new_records = []
    for r in records:
        if r.vehicle_number.upper() in existing_vehicles:
            dupes.append(r.vehicle_number)
        else:
            new_records.append(r)
            existing_vehicles.add(r.vehicle_number.upper())

    session["upd_records"].extend(new_records)

    text = f"✅ Принято {len(new_records)} записей:\n"
    for r in new_records:
        text += f"\n`{r.act_number or r.upd_number}` | {r.vehicle_number}"
    if dupes:
        text += f"\n\n⚠️ Дубли пропущены: {', '.join(dupes)}"
    if errors:
        text += "\n\n" + "\n".join(errors)
    text += f"\n\n📊 Всего: {len(session['upd_records'])}. Ещё или /go"
    await message.answer(text, parse_mode="Markdown")


@router.message(ReestrStates.waiting_input, Command("go"))
async def cmd_go(message: Message, state: FSMContext):
    """Запускает автоматическую обработку: CRM → парсинг → реестр."""
    session = _get_session(message.from_user.id)
    upd_records = session["upd_records"]

    if not upd_records:
        await message.answer("⚠️ Сначала отправь данные УПД.")
        return

    info_lines = [f"СФ {r.invoice_number} + ТС {r.vehicle_number}" for r in upd_records if r.vehicle_number]
    await message.answer(
        f"🔍 Ищу {len(info_lines)} сделок в SberCRM...\n"
        + "\n".join(info_lines)
    )

    try:
        # 1. Поиск сделок в CRM по двум триггерам: СФ + ТС
        deals = await crm.find_deals(upd_records)
        if not deals:
            await message.answer("❌ Сделки не найдены в CRM. Проверь номера ТС.")
            return

        total = len(deals)
        progress_msg = await message.answer(f"✅ Найдено {total} сделок. Скачиваю документы...\n▫️ 0/{total}")

        # 2. Скачиваем файлы из каждой сделки
        shipments = []
        cmr_records = []
        # Карта: нормализованный ТС → {"shipment": ..., "cmr": ...}
        deal_map: dict[str, dict] = {}

        for idx, deal in enumerate(deals, 1):
            deal_id = deal["id"]
            deal_name = deal["name"]
            vehicle = deal["vehicle"]
            vkey = deal["matched_target"]  # нормализованный ТС из поиска

            deal_map[vkey] = {
                "shipment": None, "cmr": None,
                "sf_number": deal.get("sf_number", 0),
                "sf_date": deal.get("sf_date", ""),
            }

            try:
                files = await crm.get_deal_files(deal_id)

                # Парсим ShipmentDetails (.docx)
                for f in files["shipments"]:
                    if f["name"].lower().endswith(".docx"):
                        data = await crm.download_file(f["key"])
                        tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
                        tmp.write(data)
                        tmp.close()
                        try:
                            sd = parse_shipment_docx(tmp.name)
                            shipments.append(sd)
                            deal_map[vkey]["shipment"] = sd
                            log.info(f"ShipmentDetails: {deal_name} → ТС {vkey}")
                        finally:
                            os.unlink(tmp.name)
                        break  # одна заявка на сделку

                # Парсим CMR (PDF/фото)
                for f in files["cmr"]:
                    data = await crm.download_file(f["key"])
                    suffix = Path(f["name"]).suffix.lower() or ".pdf"
                    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                    tmp.write(data)
                    tmp.close()
                    try:
                        cmr = await recognize_cmr(tmp.name)
                        cmr_records.append(cmr)
                        deal_map[vkey]["cmr"] = cmr
                        log.info(f"CMR {cmr.cmr_number}: {deal_name} → ТС {vkey}")
                    finally:
                        os.unlink(tmp.name)
                    break  # одна CMR на сделку

            except Exception as e:
                log.exception(f"Ошибка обработки сделки {deal_name}")
                await message.answer(f"⚠️ Ошибка в сделке {deal_name} ({vehicle}): {e}")

            # Обновляем прогресс
            bar = "▪️" * idx + "▫️" * (total - idx)
            try:
                await progress_msg.edit_text(
                    f"📦 Обработка сделок: {idx}/{total}\n{bar}"
                )
            except Exception:
                pass  # если сообщение не изменилось

        await message.answer(
            f"📦 Скачано:\n"
            f"• ShipmentDetails: {len(shipments)}\n"
            f"• CMR: {len(cmr_records)}\n\n"
            f"⏳ Генерирую реестр..."
        )

        # 3. Матчинг (привязка через deal, а не по vehicle_number)
        rows = match_data(upd_records, shipments, cmr_records, deal_map)

        # 4. Генерация xlsx
        output = Path(tempfile.mkdtemp()) / "reestr_sibur.xlsx"
        generate_reestr(rows, output)

        # 5. Сохраняем в историю
        from config import TEMP_DIR
        history_dir = Path(TEMP_DIR) / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        history_path = history_dir / f"reestr_{ts}.xlsx"
        shutil.copy2(output, history_path)
        log.info(f"Реестр сохранён в историю: {history_path}")

        # 6. Отправляем результат
        summary = generate_summary(rows)
        await message.answer(summary)

        file = FSInputFile(output, filename="reestr_sibur.xlsx")
        await message.answer_document(file, caption="📎 Реестр СИБУР")

    except Exception as e:
        log.exception("Ошибка генерации реестра")
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        await state.clear()
        sessions.pop(message.from_user.id, None)


@router.message(ReestrStates.waiting_input, Command("status"))
async def cmd_status(message: Message, state: FSMContext):
    """Показывает текущие записи в сессии."""
    session = _get_session(message.from_user.id)
    records = session["upd_records"]

    if not records:
        await message.answer("📋 Список пуст. Отправь данные в формате: `СФ ТС`", parse_mode="Markdown")
        return

    lines = [f"📋 *Текущие записи ({len(records)}):*\n"]
    for i, r in enumerate(records, 1):
        lines.append(f"{i}. СФ `{r.invoice_number}` | ТС `{r.vehicle_number}`")
    lines.append(f"\nОтправь ещё или /go для генерации")
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("template"))
async def cmd_template(message: Message):
    """Отправляет пустой шаблон реестра."""
    template = Path(TEMPLATE_PATH)
    if template.exists():
        file = FSInputFile(template, filename="reestr_template.xlsx")
        await message.answer_document(file, caption="📎 Шаблон реестра СИБУР")
    else:
        await message.answer("❌ Шаблон не найден")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    sessions.pop(message.from_user.id, None)
    await message.answer("❌ Отменено.")
