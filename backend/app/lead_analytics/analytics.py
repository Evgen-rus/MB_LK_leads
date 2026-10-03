from __future__ import annotations

import pandas as pd

from .source_utils import extract_domain
from .status_classifier import ALL_GROUPS, DEMAND_GROUPS, is_missing_status


COUNT_COL = "Всего идентификаций"
GROUP_COL = "Группа статуса"


def prepare_denominator(df: pd.DataFrame) -> pd.DataFrame:
    if GROUP_COL not in df.columns:
        return df
    return df[~df[GROUP_COL].isin({"Не учитывать", "Еще не звонили"})].copy()


def summarize(df: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    if not by:
        grouped = [("__all__", df)]
    else:
        grouped = df.groupby(by, dropna=False)
    rows = []
    for key, part in grouped:
        work = prepare_denominator(part)
        row = {}
        if by:
            keys = key if isinstance(key, tuple) else (key,)
            row.update({col: value for col, value in zip(by, keys)})
        total = len(part)
        counted_total = len(work)
        row[COUNT_COL] = counted_total
        unprocessed = (
            int(part["Исходный статус"].map(is_missing_status).sum())
            if "Исходный статус" in part.columns
            else int((part[GROUP_COL] == "Не учитывать").sum())
        )
        unprocessed += int((part[GROUP_COL] == "Еще не звонили").sum())
        demand = int(part[GROUP_COL].isin(DEMAND_GROUPS).sum())
        for group in ALL_GROUPS:
            if group == "Еще не звонили":
                continue
            count = int((work[GROUP_COL] == group).sum())
            row[group] = count
            row[f"{_short(group)} %"] = count / counted_total if counted_total else 0
            if group == "Недозвон":
                row["Не обработано"] = unprocessed
                row["Не обработано %"] = unprocessed / total if total else 0
            if group == "Уже наши / уже купил":
                row["Сигнал спроса"] = demand
                row["Сигнал спроса %"] = demand / counted_total if counted_total else 0
        rows.append(row)
    columns = list(by) + [COUNT_COL]
    for group in ALL_GROUPS:
        if group == "Еще не звонили":
            continue
        columns.extend([group, f"{_short(group)} %"])
        if group == "Недозвон":
            columns.extend(["Не обработано", "Не обработано %"])
        if group == "Уже наши / уже купил":
            columns.extend(["Сигнал спроса", "Сигнал спроса %"])
    result = pd.DataFrame(rows, columns=columns)
    return result


def _short(group: str) -> str:
    return {
        "Качественные": "Кач.",
        "Рабочий потенциал": "Рабочий потенциал",
        "Уже наши / уже купил": "Уже наши / купил",
        "Конкурент": "Конкурент",
        "Недозвон": "Недозвон",
        "Некачественные": "Некач.",
        "Не подходит по гео": "Не подходит по гео",
        "Еще не звонили": "Еще не звонили",
        "Требует проверки": "Требует проверки",
    }.get(group, group)


def add_domain(df: pd.DataFrame, source_col: str = "Полный источник") -> pd.DataFrame:
    result = df.copy()
    result["Домен"] = result[source_col].map(extract_domain)
    return result


def status_summary(df: pd.DataFrame) -> pd.DataFrame:
    work = prepare_denominator(df)
    return (
        work.groupby(["Группа статуса", "Исходный статус"], dropna=False)
        .size()
        .reset_index(name="Количество")
        .sort_values("Количество", ascending=False)
    )
