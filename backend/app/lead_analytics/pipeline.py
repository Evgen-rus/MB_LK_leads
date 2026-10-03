from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Callable

import pandas as pd

from .analytics import add_domain, status_summary, summarize
from .config import ANALYSIS_REPORTS_DIR, MATCHED_SHEET_NAME
from .excel_reader import list_sheets, read_excel_sheet
from .export_history import ExportMetadata, normalized_metadata, save_analysis_export
from .models import ColumnMapping
from .report_writer import write_excel
from .source_utils import NO_DATA, normalize_source, safe_filename
from .status_classifier import classify, sorted_rules


ProgressCallback = Callable[[str, int, int], None]


def analyze_file(
    project: str,
    file: str | Path,
    mapping: ColumnMapping,
    output_dir: str | Path,
    export_metadata: ExportMetadata | None = None,
    replace_export: bool = False,
    progress: ProgressCallback | None = None,
    storage_key: str | None = None,
    rule_snapshot: list[object] | None = None,
    run_id: str | None = None,
    settings_snapshot: dict[str, object] | None = None,
) -> Path:
    path = Path(file)
    sheets = list_sheets(path) if path.exists() else []
    sheet = MATCHED_SHEET_NAME if MATCHED_SHEET_NAME in sheets else mapping.sheet_name
    df = read_excel_sheet(path, sheet)

    data = pd.DataFrame()
    data["Дата"] = df[mapping.date_column] if mapping.date_column else pd.NaT
    parsed_dates = pd.to_datetime(data["Дата"], errors="coerce")
    data["Телефон"] = df[mapping.phone_column] if mapping.phone_column else ""
    data["Канал"] = df[mapping.channel_column] if mapping.channel_column else NO_DATA
    data["Полный источник"] = (
        df[mapping.source_column].map(normalize_source) if mapping.source_column else NO_DATA
    )
    data["Исходный статус"] = df[mapping.status_column]
    data["Комментарий"] = df[mapping.comment_column] if mapping.comment_column else ""
    total_rows = len(data)
    classifications = []
    rules = rule_snapshot if rule_snapshot is not None else sorted_rules(storage_key or project)
    if progress:
        progress("Анализ статусов", 0, total_rows)
    for position, (status, comment) in enumerate(
        zip(data["Исходный статус"], data["Комментарий"]), start=1
    ):
        classifications.append(classify(status, comment, project, rules))
        if progress:
            progress("Анализ статусов", position, total_rows)
    data["Группа статуса"] = [item[0] for item in classifications]
    data["Правило"] = [item[1] for item in classifications]
    data = add_domain(data)

    if not mapping.date_column:
        raise ValueError("Для аналитики по периодам выберите колонку даты")
    if not export_metadata:
        raise ValueError("Для аналитики добавьте хотя бы один период")
    metadata = normalized_metadata(export_metadata, path.name)
    totals = []
    period_results = []
    report_sheets: dict[str, pd.DataFrame | list[str]] = {}
    used_sheet_names = {"Итог"}
    for period in metadata.periods:
        start = pd.Timestamp(period.period_start)
        end = pd.Timestamp(period.period_end) + pd.Timedelta(days=1)
        period_data = data[(parsed_dates >= start) & (parsed_dates < end)].copy()
        label = f"{period.period_start} - {period.period_end}"
        short_label = f"{start:%d.%m}-{pd.Timestamp(period.period_end):%d.%m}"

        total = summarize(period_data, [])
        total.insert(0, "Проект", project)
        total.insert(0, "Период", label)
        totals.append(total)
        by_domain_channel = summarize(period_data, ["Домен", "Канал"]).sort_values(
            ["Кач. %", "Сигнал спроса %", "Всего идентификаций"],
            ascending=[False, False, False],
        )
        by_source_channel = summarize(period_data, ["Полный источник", "Канал"]).sort_values(
            ["Кач. %", "Сигнал спроса %", "Всего идентификаций"],
            ascending=[False, False, False],
        )
        channels = summarize(period_data, ["Канал"]).sort_values(
            ["Сигнал спроса %", "Недозвон %"],
            ascending=[False, True],
        )
        breakdowns = {
            "domain_channel": by_domain_channel,
            "source_channel": by_source_channel,
            "channel": channels,
        }
        period_results.append((period, total, breakdowns))
        for prefix, frame in (
            ("По доменам", by_domain_channel),
            ("По источникам", by_source_channel),
            ("По каналам", channels),
            ("Статусы", status_summary(period_data)),
            ("Данные", period_data),
        ):
            name = _unique_sheet_name(f"{prefix} {short_label}", used_sheet_names)
            used_sheet_names.add(name)
            report_sheets[name] = frame

    output = Path(output_dir) / f"{safe_filename(project)}_аналитика.xlsx"
    if progress:
        progress("Формирование аналитического отчёта", total_rows, total_rows)
    written = write_excel(output, {"Итог": pd.concat(totals, ignore_index=True), **report_sheets})
    ANALYSIS_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_file_name = f"{uuid.uuid4().hex}.xlsx"
    archived_report = ANALYSIS_REPORTS_DIR / report_file_name
    shutil.copy2(written, archived_report)
    try:
        save_analysis_export(
            storage_key or project,
            metadata,
            period_results,
            report_file_name=report_file_name,
            replace=replace_export,
            run_id=run_id,
            settings_snapshot=settings_snapshot,
        )
    except Exception:
        archived_report.unlink(missing_ok=True)
        raise
    return written


def _unique_sheet_name(name: str, used: set[str]) -> str:
    if name not in used:
        return name
    index = 2
    while f"{name} {index}" in used:
        index += 1
    return f"{name} {index}"
