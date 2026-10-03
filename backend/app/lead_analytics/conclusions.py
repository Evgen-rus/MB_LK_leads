from __future__ import annotations

import pandas as pd


def build_conclusions(total: pd.DataFrame, channels: pd.DataFrame) -> list[str]:
    if total.empty:
        return ["Нет данных для анализа."]
    row = total.iloc[0]
    conclusions = []
    quality = float(row.get("Кач. %", 0) or 0)
    demand = float(row.get("Сигнал спроса %", 0) or 0)
    missed = float(row.get("Недозвон %", 0) or 0)
    needs_review = float(row.get("Требует проверки %", 0) or 0)
    if quality >= 0.03:
        conclusions.append("Качественных лидов 3% или выше: показатель выглядит хорошим для сырых данных.")
    if demand >= 0.10:
        conclusions.append("Сигнал спроса 10% или выше: источник/проект показывает заметный спрос.")
    if missed > 0.25:
        conclusions.append("Недозвон выше 25%: сначала стоит проверить дозвон и процесс обзвона.")
    if needs_review > 0:
        conclusions.append("Есть статусы, требующие ручной классификации.")
    if len(channels) <= 1:
        conclusions.append("Канал один или каналов нет: сравнение каналов не строим.")
    return conclusions or ["Критичных выводов по текущим правилам нет."]
