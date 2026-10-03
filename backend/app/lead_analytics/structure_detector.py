from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .config import MATCHED_SHEET_NAME
from .excel_reader import list_sheets, read_excel_sheet
from .models import ColumnMapping


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip().casefold())


def _find_by_names(columns: list[str], candidates: list[str]) -> str | None:
    normalized = {_norm(col): col for col in columns}
    for candidate in candidates:
        if _norm(candidate) in normalized:
            return normalized[_norm(candidate)]
    for col in columns:
        ncol = _norm(col)
        if any(_norm(candidate) in ncol for candidate in candidates):
            return col
    return None


def _looks_channel(series: pd.Series) -> bool:
    values = series.dropna().astype(str).str.strip().head(50).tolist()
    if not values:
        return False
    good = sum(1 for value in values if value in {"A", "B", "C", "D"})
    return good / len(values) >= 0.6


def _has_domain_like(series: pd.Series) -> bool:
    values = series.dropna().astype(str).head(50).tolist()
    return any("." in value or "_" in value or re.search(r"\d{7,}", value) for value in values)


def choose_data_sheet(path: str | Path) -> str:
    sheets = list_sheets(path)
    best_sheet = sheets[0]
    best_score = -1
    for sheet in sheets:
        df = read_excel_sheet(path, sheet)
        columns = list(df.columns)
        score = len(columns) + min(len(df), 10)
        known = ["статус", "stage", "lk", "телефон", "источник", "utm", "канал"]
        score += sum(3 for col in columns for word in known if word in _norm(col))
        if score > best_score:
            best_sheet = sheet
            best_score = score
    return best_sheet


def choose_analyze_sheet(path: str | Path) -> str:
    sheets = list_sheets(path)
    if MATCHED_SHEET_NAME in sheets:
        return MATCHED_SHEET_NAME
    return choose_data_sheet(path)


def detect_analyze_mapping(path: str | Path, sheet_name: str | None = None) -> ColumnMapping:
    sheets = list_sheets(path)
    if MATCHED_SHEET_NAME in sheets:
        sheet = MATCHED_SHEET_NAME
    else:
        sheet = sheet_name or choose_data_sheet(path)
    df = read_excel_sheet(path, sheet)
    columns = list(df.columns)
    date_col = _find_by_names(columns, ["Дата", "Дата создания", "date", "created"])
    phone_col = _find_by_names(columns, ["Телефон", "Номера", "phone"])
    status_col = _find_by_names(
        columns,
        [
            "Статус",
            "Статус (заполняется клиентом)",
            "Стадия сделки",
            "status",
            "stage",
            "статус клиента",
        ],
    )
    comment_col = _find_by_names(columns, ["Комментарий", "Комментарии", "comment"])

    channel_col = _find_by_names(columns, ["Канал"])
    source_candidates = [col for col in columns if _norm(col) in {"источник", "источники", "полный источник", "source", "utm campaign"}]
    if not channel_col:
        for col in source_candidates:
            if _looks_channel(df[col]):
                channel_col = col
                break
    source_col = None
    for col in source_candidates:
        if col != channel_col and _has_domain_like(df[col]):
            source_col = col
            break
    if not source_col:
        source_col = _find_by_names(columns, ["Полный источник", "Источники", "UTM Campaign", "source"])

    return ColumnMapping(
        sheet_name=sheet,
        date_column=date_col,
        phone_column=phone_col,
        channel_column=channel_col,
        source_column=source_col,
        status_column=status_col,
        comment_column=comment_col,
    )


def prepare_analyze_mapping(path: str | Path, mapping: ColumnMapping | None = None) -> ColumnMapping:
    detected = detect_analyze_mapping(path)
    if mapping is None:
        return detected
    mapping.sheet_name = detected.sheet_name
    columns = set(read_excel_sheet(path, mapping.sheet_name).columns)
    required = [
        mapping.date_column,
        mapping.phone_column,
        mapping.channel_column,
        mapping.source_column,
        mapping.status_column,
        mapping.comment_column,
    ]
    if any(column and column not in columns for column in required):
        return detected
    return mapping


def detect_match_mapping(path: str | Path, role: str, sheet_name: str | None = None) -> ColumnMapping:
    sheet = sheet_name or choose_data_sheet(path)
    df = read_excel_sheet(path, sheet)
    columns = list(df.columns)
    lkid_col = _find_by_names(columns, ["LKID", "lk id", "lk_id", "lead id"])
    phone_col = _find_by_names(columns, ["Телефон", "Номера", "phone"])
    date_col = _find_by_names(columns, ["Дата", "Дата создания", "date", "created"])
    source_col = _find_by_names(columns, ["Источники", "Полный источник", "Источник", "UTM Campaign", "source"])
    status_col = None
    comment_col = None
    project_col = None
    if role == "client":
        status_col = _find_by_names(columns, ["Стадия сделки", "Статус", "status", "stage"])
        comment_col = _find_by_names(columns, ["Комментарий", "Комментарии", "comment"])
    else:
        project_col = _find_by_names(columns, ["Проект", "Клиент", "project"])
    return ColumnMapping(
        sheet_name=sheet,
        lkid_column=lkid_col,
        phone_column=phone_col,
        date_column=date_col,
        source_column=source_col,
        status_column=status_col,
        comment_column=comment_col,
        project_column=project_col,
    )
