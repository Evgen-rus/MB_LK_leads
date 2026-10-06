from __future__ import annotations

from pathlib import Path
from typing import Callable

import pandas as pd

from .config import MATCHED_SHEET_NAME
from .excel_reader import read_excel_sheet
from .models import ColumnMapping
from .phone_utils import normalize_phone
from .report_writer import write_excel
from .source_utils import extract_lkid_from_source, safe_filename


ProgressCallback = Callable[[str, int, int], None]


def _clean_id(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _series(df: pd.DataFrame, column: str | None, default: object = "") -> pd.Series:
    if column and column in df.columns:
        return df[column]
    return pd.Series([default] * len(df), index=df.index)


def _prepare_lk(df: pd.DataFrame, mapping: ColumnMapping) -> pd.DataFrame:
    result = df.copy()
    result["_row"] = range(1, len(result) + 1)
    result["_lkid"] = _series(result, mapping.lkid_column).map(_clean_id)
    result["_phone"] = _series(result, mapping.phone_column).map(normalize_phone)
    result["_date"] = pd.to_datetime(_series(result, mapping.date_column), errors="coerce")
    result["_source"] = _series(result, mapping.source_column).fillna("").astype(str)
    return result


def _prepare_client(df: pd.DataFrame, mapping: ColumnMapping) -> pd.DataFrame:
    result = df.copy()
    result["_row"] = range(1, len(result) + 1)
    result["_lkid"] = _series(result, mapping.lkid_column).map(_clean_id)
    result["_lkid_from_source"] = _series(result, mapping.source_column).map(extract_lkid_from_source)
    result["_phone"] = _series(result, mapping.phone_column).map(normalize_phone)
    result["_date"] = pd.to_datetime(_series(result, mapping.date_column), errors="coerce")
    result["_status"] = _series(result, mapping.status_column).fillna("").astype(str)
    result["_comment"] = _series(result, mapping.comment_column).fillna("").astype(str)
    result["_source"] = _series(result, mapping.source_column).fillna("").astype(str)
    return result


def _build_index(client: pd.DataFrame, key_col: str) -> dict[str, pd.Series]:
    rows = client[client[key_col] != ""].copy()
    if rows.empty:
        return {}

    # The original rule is: prefer the latest dated row, then the latest source row.
    # Sorting all rows once avoids repeating a DataFrame sort for every individual key.
    rows["_has_date"] = rows["_date"].notna()
    ordered = rows.sort_values([key_col, "_has_date", "_date", "_row"], kind="stable")
    selected = ordered.drop_duplicates(subset=key_col, keep="last")
    return {str(row[key_col]): row for _, row in selected.iterrows()}


def match_files(
    project: str,
    lk_file: str | Path,
    client_file: str | Path,
    lk_mapping: ColumnMapping,
    client_mapping: ColumnMapping,
    output_dir: str | Path,
    progress: ProgressCallback | None = None,
) -> Path:
    lk_raw = read_excel_sheet(lk_file, lk_mapping.sheet_name)
    client_raw = read_excel_sheet(client_file, client_mapping.sheet_name)
    lk = _prepare_lk(lk_raw, lk_mapping)
    client = _prepare_client(client_raw, client_mapping)

    by_lkid = _build_index(client, "_lkid")
    by_source_lkid = _build_index(client, "_lkid_from_source")
    by_phone = _build_index(client, "_phone")
    lk_lkids = set(lk["_lkid"])
    lk_phones = set(lk["_phone"])

    candidates = {}
    for kind, column in (("LKID", "_lkid"), ("LKID из источника", "_lkid_from_source"), ("Телефон", "_phone")):
        candidates[kind] = {
            str(key): rows for key, rows in client[client[column] != ""].groupby(column)
            if len(rows) > 1
        }
    ambiguous_rows = []
    ambiguous_count = 0
    matched_rows = []
    unmatched_lk = []
    used_client_rows = set()
    match_counts = {"LKID": 0, "LKID из источника": 0, "Телефон": 0}
    total_rows = len(lk) + len(client)
    if progress:
        progress("Сопоставление строк", 0, total_rows)

    for position, (_, lk_row) in enumerate(lk.iterrows(), start=1):
        client_row = None
        match_key = ""
        match_type = ""
        if lk_row["_lkid"] and lk_row["_lkid"] in by_lkid:
            client_row = by_lkid[lk_row["_lkid"]]
            match_key = lk_row["_lkid"]
            match_type = "LKID"
        elif lk_row["_lkid"] and lk_row["_lkid"] in by_source_lkid:
            client_row = by_source_lkid[lk_row["_lkid"]]
            match_key = lk_row["_lkid"]
            match_type = "LKID из источника"
        elif lk_row["_phone"] and lk_row["_phone"] in by_phone:
            client_row = by_phone[lk_row["_phone"]]
            match_key = lk_row["_phone"]
            match_type = "Телефон"

        if client_row is None:
            item = lk_row.to_dict()
            item["Причина"] = "ключ не найден"
            unmatched_lk.append(item)
            if progress:
                progress("Сопоставление строк", position, total_rows)
            continue

        alternatives = candidates[match_type].get(match_key)
        if alternatives is not None:
            ambiguous_count += 1
            for _, alternative in alternatives.iterrows():
                ambiguous_rows.append({
                    "Строка ЛК": lk_row["_row"], "LKID": lk_row["_lkid"],
                    "Тип ключа": match_type, "Ключ": match_key,
                    "Строка клиента": alternative["_row"], "Источник клиента": alternative["_source"],
                    "Статус клиента": alternative["_status"], "Дата клиента": alternative["_date"],
                    "Выбрана строка": "да" if alternative["_row"] == client_row["_row"] else "нет",
                    "Правило выбора": "последняя дата, затем последняя строка",
                })
        used_client_rows.add(int(client_row["_row"]))
        match_counts[match_type] += 1
        item = lk_row.to_dict()
        item["LKID"] = lk_row["_lkid"]
        item["Нормализованный телефон"] = lk_row["_phone"]
        item["Дата из ЛК"] = lk_row["_date"]
        item["Полный источник из ЛК"] = lk_row["_source"]
        item["Статус клиента"] = client_row["_status"]
        item["Комментарий клиента"] = client_row["_comment"]
        item["Дата клиента"] = client_row["_date"]
        item["Ключ сопоставления"] = match_key
        item["Тип сопоставления"] = match_type
        item["Строка клиента"] = client_row["_row"]
        item["Неоднозначное сопоставление"] = "да" if alternatives is not None else "нет"
        item["Комментарий сопоставления"] = f"Сопоставлено по {match_type}"
        matched_rows.append(item)
        if progress:
            progress("Сопоставление строк", position, total_rows)

    unmatched_client = []
    for position, (_, row) in enumerate(client.iterrows(), start=1):
        if int(row["_row"]) in used_client_rows:
            continue
        item = row.to_dict()
        if row["_lkid"] and row["_lkid"] not in lk_lkids:
            reason = "нет такого LKID в ЛК"
        elif row["_lkid_from_source"] and row["_lkid_from_source"] not in lk_lkids:
            reason = "нет такого LKID в ЛК"
        elif row["_phone"] and row["_phone"] not in lk_phones:
            reason = "нет такого телефона в ЛК"
        elif not row["_lkid"] and not row["_lkid_from_source"] and not row["_phone"]:
            reason = "нет ключа для сопоставления"
        else:
            reason = "ключ не найден"
        item["Причина"] = reason
        unmatched_client.append(item)
        if progress:
            progress("Проверка строк клиента", len(lk) + position, total_rows)

    check = pd.DataFrame(
        [
            ["Проект", project],
            ["Файл ЛК", str(lk_file)],
            ["Файл клиента", str(client_file)],
            ["Строк в ЛК", len(lk)],
            ["Строк у клиента", len(client)],
            ["Сопоставлено", len(matched_rows)],
            ["Неоднозначные сопоставления", ambiguous_count],
            ["Не сопоставлено из ЛК", len(unmatched_lk)],
            ["Не сопоставлено от клиента", len(unmatched_client)],
            ["Сопоставлено по LKID", match_counts["LKID"]],
            ["Сопоставлено по LKID из источника", match_counts["LKID из источника"]],
            ["Сопоставлено по телефону", match_counts["Телефон"]],
        ],
        columns=["Показатель", "Значение"],
    )
    ratio = len(matched_rows) / len(lk) if len(lk) else 0
    check = pd.concat(
        [
            check,
            pd.DataFrame(
                [
                    ["Вывод", f"Сопоставлено {ratio:.1%} строк ЛК."],
                    ["Вывод", "Можно запускать analyze." if matched_rows else "Аналитику запускать рано: нет сопоставлений."],
                ],
                columns=["Показатель", "Значение"],
            ),
        ],
        ignore_index=True,
    )

    output = Path(output_dir) / f"{safe_filename(project)}_сопоставление.xlsx"
    if progress:
        progress("Формирование отчёта сопоставления", total_rows, total_rows)
    return write_excel(
        output,
        {
            MATCHED_SHEET_NAME: pd.DataFrame(matched_rows),
            "Не сопоставлено из ЛК": pd.DataFrame(unmatched_lk),
            "Не сопоставлено от клиента": pd.DataFrame(unmatched_client),
            "Неоднозначные сопоставления": pd.DataFrame(ambiguous_rows, columns=["Строка ЛК", "LKID", "Тип ключа", "Ключ", "Строка клиента", "Источник клиента", "Статус клиента", "Дата клиента", "Выбрана строка", "Правило выбора"]),
            "Проверка": check,
            "Как читать сопоставление": pd.DataFrame([
                ["Версия правил", "2: LKID из источника — 8 цифр с первой 3 после последнего подчёркивания"],
                ["Порядок поиска", "LKID → LKID из источника → нормализованный телефон. Используется первый найденный тип ключа."],
                ["Сопоставленные", "Одна строка на сопоставленную строку ЛК. Строка клиента указывает выбранную запись."],
                ["Неоднозначные сопоставления", "Все кандидаты только по фактически использованному ключу, отдельно для каждой строки ЛК. Выбрана строка = да — запись, из которой взят статус."],
                ["Счётчик неоднозначных", "Количество строк ЛК с несколькими кандидатами, а не количество лишних строк клиента. Повторы по неиспользованным ключам не считаются."],
                ["Правило выбора", "Последняя корректная дата клиента; при одинаковых или отсутствующих датах — последняя строка клиента."],
                ["Номера строк", "Строка ЛК: _row; Строка клиента: номер записи после удаления полностью пустых строк. Нумерация с 1, без заголовка; это не физический номер строки Excel."],
                ["Не сопоставлено от клиента", "Записи клиента, не выбранные ни для одной строки ЛК, включая невыбранных кандидатов. Они не добавляются отдельно в аналитику."],
                ["Проверка результата", "Сопоставлено + Не сопоставлено из ЛК = Строк в ЛК. Проверять неоднозначные по Строка ЛК, Тип ключа, Ключ и выбранному статусу."],
                ["Параметры ЛК", str(lk_mapping)],
                ["Параметры клиента", str(client_mapping)],
            ], columns=["Раздел", "Пояснение"]),
        },
    )
