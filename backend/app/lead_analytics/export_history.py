from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from . import db
from .config import ANALYSIS_REPORTS_DIR
from .analytics import COUNT_COL
from .report_writer import write_excel
from .source_utils import safe_filename
from .status_classifier import ALL_GROUPS

QUALITY_COUNT_COL = ALL_GROUPS[0]
MISSED_COUNT_COL = "Недозвон"
QUALITY_RATE_COL = "Кач. %"
MISSED_RATE_COL = "Недозвон %"
DEMAND_COUNT_COL = "Сигнал спроса"
DEMAND_RATE_COL = "Сигнал спроса %"


def analysis_report_path(report_file_name: str | None) -> Path | None:
    if not report_file_name or Path(report_file_name).name != report_file_name:
        return None
    if Path(report_file_name).suffix.lower() != ".xlsx":
        return None
    token = Path(report_file_name).stem
    if len(token) != 32:
        return None
    try:
        uuid.UUID(hex=token)
    except ValueError:
        return None
    return ANALYSIS_REPORTS_DIR / report_file_name
SMALL_SAMPLE_THRESHOLD = 30


@dataclass
class ExportMetadata:
    export_number: int | None
    periods: list["ExportPeriod"]
    analysis_date: str | None = None
    source_file_name: str = ""


@dataclass(frozen=True)
class ExportPeriod:
    period_start: str
    period_end: str


def today_text() -> str:
    return date.today().isoformat()


def normalize_date(value: str, field_name: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field_name} должна быть в формате YYYY-MM-DD") from exc


def normalized_metadata(metadata: ExportMetadata, source_file_name: str) -> ExportMetadata:
    if metadata.export_number is not None and metadata.export_number <= 0:
        raise ValueError("Номер выгрузки должен быть положительным")
    if not metadata.periods:
        raise ValueError("Добавьте хотя бы один период")
    periods = []
    for period in metadata.periods:
        period_start = normalize_date(period.period_start, "Дата начала периода")
        period_end = normalize_date(period.period_end, "Дата конца периода")
        if period_start > period_end:
            raise ValueError("Дата начала периода не может быть позже даты конца")
        periods.append(ExportPeriod(period_start, period_end))
    if len(set(periods)) != len(periods):
        raise ValueError("Одинаковые периоды нельзя добавлять дважды")
    analysis_date = normalize_date(metadata.analysis_date, "Дата анализа") if metadata.analysis_date else today_text()
    return ExportMetadata(
        export_number=metadata.export_number,
        periods=periods,
        analysis_date=analysis_date,
        source_file_name=metadata.source_file_name or source_file_name,
    )


def save_analysis_export(
    project: str,
    metadata: ExportMetadata,
    period_results: list[tuple[ExportPeriod, pd.DataFrame, dict[str, pd.DataFrame]]],
    report_file_name: str | None = None,
    replace: bool = False,
    run_id: str | None = None,
    settings_snapshot: dict[str, Any] | None = None,
) -> int:
    db.ensure_project(project)
    meta = normalized_metadata(metadata, metadata.source_file_name)
    first_period, first_total, _ = period_results[0]
    total_metrics = _metrics_from_row(first_total.iloc[0] if not first_total.empty else {})
    stamp = db.now_text()
    replaced_report_path: Path | None = None

    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        export_number = meta.export_number
        if export_number is None:
            export_number = int(
                conn.execute(
                    "SELECT COALESCE(MAX(export_number), 0) + 1 FROM analysis_exports WHERE project_code = ?",
                    (project,),
                ).fetchone()[0]
            )
        existing = conn.execute(
            """
            SELECT id, report_file_name FROM analysis_exports
            WHERE project_code = ? AND export_number = ?
            """,
            (project, export_number),
        ).fetchone()
        if existing and not replace:
            raise ValueError(
                f"Выгрузка №{export_number} уже сохранена для проекта {project}. "
                "Удалите ее или сохраните с заменой."
            )
        if existing and replace:
            replaced_report_path = analysis_report_path(existing["report_file_name"])
            conn.execute("DELETE FROM analysis_export_breakdowns WHERE export_id = ?", (existing["id"],))
            conn.execute("DELETE FROM analysis_export_periods WHERE export_id = ?", (existing["id"],))
            conn.execute("DELETE FROM analysis_exports WHERE id = ?", (existing["id"],))

        cursor = conn.execute(
            """
            INSERT INTO analysis_exports(
                project_code, export_number, period_start, period_end, analysis_date,
                source_file_name, report_file_name, total_count, missed_count, missed_rate,
                quality_count, quality_rate, demand_count, demand_rate, metrics_json, settings_json, run_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project,
                export_number,
                first_period.period_start,
                first_period.period_end,
                meta.analysis_date,
                meta.source_file_name,
                report_file_name,
                total_metrics["total_count"],
                total_metrics["missed_count"],
                total_metrics["missed_rate"],
                total_metrics["quality_count"],
                total_metrics["quality_rate"],
                total_metrics["demand_count"],
                total_metrics["demand_rate"],
                json.dumps(_json_safe_metrics(first_total.iloc[0] if not first_total.empty else {}), ensure_ascii=False),
                json.dumps(settings_snapshot or {}, ensure_ascii=False),
                run_id,
                stamp,
                stamp,
            ),
        )
        export_id = int(cursor.lastrowid)
        for period_index, (period, total, breakdowns) in enumerate(period_results, start=1):
            metrics = _metrics_from_row(total.iloc[0] if not total.empty else {})
            period_cursor = conn.execute(
                """
                INSERT INTO analysis_export_periods(
                    export_id, period_index, period_start, period_end,
                    total_count, missed_count, missed_rate, quality_count, quality_rate,
                    demand_count, demand_rate, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    export_id,
                    period_index,
                    period.period_start,
                    period.period_end,
                    metrics["total_count"],
                    metrics["missed_count"],
                    metrics["missed_rate"],
                    metrics["quality_count"],
                    metrics["quality_rate"],
                    metrics["demand_count"],
                    metrics["demand_rate"],
                    json.dumps(_json_safe_metrics(total.iloc[0] if not total.empty else {}), ensure_ascii=False),
                ),
            )
            period_id = int(period_cursor.lastrowid)
            for breakdown_type, frame in breakdowns.items():
                for item in _breakdown_rows(breakdown_type, frame):
                    conn.execute(
                        """
                        INSERT INTO analysis_export_breakdowns(
                            export_id, period_id, breakdown_type, dimension_1, dimension_2,
                            total_count, missed_count, missed_rate, quality_count, quality_rate,
                            demand_count, demand_rate, metrics_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            export_id,
                            period_id,
                            breakdown_type,
                            item["dimension_1"],
                            item["dimension_2"],
                            item["total_count"],
                            item["missed_count"],
                            item["missed_rate"],
                            item["quality_count"],
                            item["quality_rate"],
                            item["demand_count"],
                            item["demand_rate"],
                            json.dumps(item["metrics"], ensure_ascii=False),
                        ),
                    )
    if replaced_report_path:
        try:
            replaced_report_path.unlink(missing_ok=True)
        except OSError:
            pass
    return export_id


def delete_analysis_export(project: str, export_number: int) -> bool:
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT id, report_file_name FROM analysis_exports
            WHERE project_code = ? AND export_number = ?
            """,
            (project, export_number),
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM analysis_export_breakdowns WHERE export_id = ?", (row["id"],))
        conn.execute("DELETE FROM analysis_export_periods WHERE export_id = ?", (row["id"],))
        conn.execute("DELETE FROM analysis_exports WHERE id = ?", (row["id"],))
    report_path = analysis_report_path(row["report_file_name"])
    if report_path:
        try:
            report_path.unlink(missing_ok=True)
        except OSError:
            pass
    return True


def list_exports(project: str) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM analysis_exports
            WHERE project_code = ?
            ORDER BY export_number ASC
            """,
            (project,),
        ).fetchall()
        periods = conn.execute(
            """
            SELECT p.* FROM analysis_export_periods p
            JOIN analysis_exports e ON e.id = p.export_id
            WHERE e.project_code = ?
            ORDER BY e.export_number ASC, p.period_index ASC
            """,
            (project,),
        ).fetchall()
    periods_by_export: dict[int, list[dict[str, Any]]] = {}
    for period in periods:
        periods_by_export.setdefault(int(period["export_id"]), []).append(_period_row(period))
    result = []
    for row in rows:
        item = _export_row(row)
        item["periods"] = periods_by_export.get(int(row["id"])) or [_legacy_period(item)]
        result.append(item)
    return result


def build_project_summary(project: str) -> dict[str, pd.DataFrame | list[str]]:
    exports = list_exports(project)
    if not exports:
        return {"Выводы": [f"По проекту {project} пока нет сохраненных выгрузок."]}
    return {
        "Реестр выгрузок": build_registry(_flatten_exports(exports)),
        "Динамика к предыдущей": build_export_dynamics(_flatten_exports(exports)),
        "Источники + каналы": build_breakdown_dynamics(project, "source_channel"),
        "Домены + каналы": build_breakdown_dynamics(project, "domain_channel"),
        "Каналы": build_breakdown_dynamics(project, "channel"),
        "Выводы": build_conclusions(_flatten_exports(exports), project),
    }


def write_project_summary(project: str, output_dir: str | Path) -> Path:
    output = Path(output_dir) / f"{safe_filename(project)}_сводка_выгрузок.xlsx"
    return write_excel(output, build_project_summary(project))


def write_project_comparison(project: str, output_dir: str | Path) -> Path:
    exports = list_exports(project)
    flattened = _flatten_exports(exports)
    output = Path(output_dir) / f"{safe_filename(project)}_сравнение_выгрузок.xlsx"
    sheets: dict[str, pd.DataFrame | list[str]] = {
        "Реестр выгрузок": build_registry(flattened) if exports else pd.DataFrame(),
        "Динамика к предыдущей": build_export_dynamics(flattened) if exports else pd.DataFrame(),
        "Выводы": build_conclusions(flattened, project) if exports else [f"По проекту {project} пока нет сохраненных выгрузок."],
    }
    return write_excel(output, sheets)


def build_registry(exports: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    previous: dict[str, Any] | None = None
    for item in exports:
        rows.append(
            {
                "Номер выгрузки": item["export_number"],
                "Период": f'{item["period_start"]} - {item["period_end"]}',
                "Дата анализа": item["analysis_date"],
                "Имя файла": item["source_file_name"],
                "Всего строк": item["total_count"],
                "Недозвон, шт.": item["missed_count"],
                "Недозвон, %": item["missed_rate"],
                "Качественные, шт.": item["quality_count"],
                "Качественные, %": item["quality_rate"],
                "Сигнал спроса, шт.": item["demand_count"],
                "Сигнал спроса, %": item["demand_rate"],
                "Краткий вывод": _brief_export_conclusion(item, previous),
            }
        )
        previous = item
    return pd.DataFrame(rows)


def build_export_dynamics(exports: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    previous: dict[str, Any] | None = None
    for item in exports:
        if previous is None:
            rows.append(
                {
                    "Номер выгрузки": item["export_number"],
                    "Предыдущая выгрузка": "",
                    "Изм. недозвона, п.п.": "",
                    "Изм. качественных, п.п.": "",
                    "Изм. сигнала спроса, п.п.": "",
                    "Всего строк": item["total_count"],
                    "Пред. всего строк": "",
                    "Комментарий": "Базовая выгрузка для дальнейшего сравнения.",
                }
            )
        else:
            rows.append(
                {
                    "Номер выгрузки": item["export_number"],
                    "Предыдущая выгрузка": previous["export_number"],
                    "Изм. недозвона, п.п.": _pp(item["missed_rate"] - previous["missed_rate"]),
                    "Изм. качественных, п.п.": _pp(item["quality_rate"] - previous["quality_rate"]),
                    "Изм. сигнала спроса, п.п.": _pp(item["demand_rate"] - previous["demand_rate"]),
                    "Всего строк": item["total_count"],
                    "Пред. всего строк": previous["total_count"],
                    "Комментарий": _comparison_comment(item, previous),
                }
            )
        previous = item
    return pd.DataFrame(rows)


def build_breakdown_dynamics(project: str, breakdown_type: str) -> pd.DataFrame:
    rows = _load_breakdowns(project, breakdown_type)
    label_1, label_2 = {
        "source_channel": ("Полный источник", "Канал"),
        "domain_channel": ("Домен", "Канал"),
        "channel": ("Канал", ""),
    }[breakdown_type]
    previous_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    result = []
    for item in rows:
        key = (item["dimension_1"] or "", item["dimension_2"] or "")
        previous = previous_by_key.get(key)
        row = {
            "Номер выгрузки": item["export_number"],
            "Период": f'{item["period_start"]} - {item["period_end"]}',
            label_1: item["dimension_1"],
            "Всего строк": item["total_count"],
            "Недозвон, шт.": item["missed_count"],
            "Недозвон, %": item["missed_rate"],
            "Качественные, шт.": item["quality_count"],
            "Качественные, %": item["quality_rate"],
            "Сигнал спроса, шт.": item["demand_count"],
            "Сигнал спроса, %": item["demand_rate"],
            "Изм. недозвона, п.п.": _pp(item["missed_rate"] - previous["missed_rate"]) if previous else "",
            "Изм. качественных, п.п.": _pp(item["quality_rate"] - previous["quality_rate"]) if previous else "",
            "Изм. сигнала спроса, п.п.": _pp(item["demand_rate"] - previous["demand_rate"]) if previous else "",
            "Комментарий": _comparison_comment(item, previous) if previous else "Первое появление среза в истории.",
        }
        if label_2:
            row = {
                "Номер выгрузки": row.pop("Номер выгрузки"),
                "Период": row.pop("Период"),
                label_1: row.pop(label_1),
                label_2: item["dimension_2"],
                **row,
            }
        result.append(row)
        previous_by_key[key] = item
    return pd.DataFrame(result)


def build_conclusions(exports: list[dict[str, Any]], project: str) -> list[str]:
    if not exports:
        return [f"По проекту {project} пока нет сохраненных выгрузок."]

    conclusions = []
    best_demand = max(exports, key=lambda item: item["demand_rate"])
    worst_missed = max(exports, key=lambda item: item["missed_rate"])
    conclusions.append(
        f"Лучшая выгрузка по сигналу спроса %: №{best_demand['export_number']} "
        f"({best_demand['demand_rate']:.2%}, объем {best_demand['total_count']})."
    )
    conclusions.append(
        f"Самый высокий недозвон %: выгрузка №{worst_missed['export_number']} "
        f"({worst_missed['missed_rate']:.2%}, объем {worst_missed['total_count']})."
    )

    for previous, item in zip(exports, exports[1:]):
        quality_delta = item["quality_rate"] - previous["quality_rate"]
        demand_delta = item["demand_rate"] - previous["demand_rate"]
        missed_delta = item["missed_rate"] - previous["missed_rate"]
        if quality_delta:
            direction = "выросла" if quality_delta > 0 else "упала"
            conclusions.append(
                f"В выгрузке №{item['export_number']} доля качественных {direction} "
                f"на {abs(_pp(quality_delta)):.2f} п.п. к №{previous['export_number']}."
            )
        if demand_delta:
            direction = "вырос" if demand_delta > 0 else "упал"
            conclusions.append(
                f"В выгрузке №{item['export_number']} сигнал спроса {direction} "
                f"на {abs(_pp(demand_delta)):.2f} п.п. к №{previous['export_number']}."
            )
        if missed_delta > 0:
            conclusions.append(
                f"В выгрузке №{item['export_number']} недозвон вырос на {_pp(missed_delta):.2f} п.п.; дозвон стал хуже."
            )
        if item["total_count"] > previous["total_count"] and item["demand_rate"] < previous["demand_rate"]:
            conclusions.append(
                f"Выгрузка №{item['export_number']}: объем больше, чем в №{previous['export_number']}, "
                "но сигнал спроса % ниже. Объем вырос, эффективность снизилась."
            )
        if item["total_count"] > previous["total_count"] and item["quality_rate"] < previous["quality_rate"]:
            conclusions.append(
                f"Выгрузка №{item['export_number']}: качественных лидов по объему может быть больше, "
                "но доля качественных ниже, поэтому качество выборки снизилось."
            )
        if item["total_count"] < SMALL_SAMPLE_THRESHOLD and (
            item["demand_rate"] > previous["demand_rate"] or item["quality_rate"] > previous["quality_rate"]
        ):
            conclusions.append(
                f"Выгрузка №{item['export_number']} показывает лучший процент, но объем "
                f"{item['total_count']} меньше {SMALL_SAMPLE_THRESHOLD}; вывод осторожный."
            )

    if len(exports) == 1:
        conclusions.append("Сохранена одна выгрузка: динамику можно будет оценить после следующего анализа.")
    return conclusions


def _load_breakdowns(project: str, breakdown_type: str) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                e.export_number,
                COALESCE(p.period_start, e.period_start) AS period_start,
                COALESCE(p.period_end, e.period_end) AS period_end,
                b.dimension_1, b.dimension_2,
                b.total_count, b.missed_count, b.missed_rate,
                b.quality_count, b.quality_rate, b.demand_count, b.demand_rate
            FROM analysis_export_breakdowns b
            JOIN analysis_exports e ON e.id = b.export_id
            LEFT JOIN analysis_export_periods p ON p.id = b.period_id
            WHERE e.project_code = ? AND b.breakdown_type = ?
            ORDER BY b.dimension_1 COLLATE NOCASE, b.dimension_2 COLLATE NOCASE, e.export_number ASC
            """,
            (project, breakdown_type),
        ).fetchall()
    return [dict(row) for row in rows]


def _breakdown_rows(breakdown_type: str, frame: pd.DataFrame) -> list[dict[str, Any]]:
    dimensions = {
        "source_channel": ["Полный источник", "Канал"],
        "domain_channel": ["Домен", "Канал"],
        "channel": ["Канал"],
    }[breakdown_type]
    result = []
    for _, row in frame.iterrows():
        metrics = _metrics_from_row(row)
        result.append(
            {
                **metrics,
                "dimension_1": _clean_dimension(row.get(dimensions[0])),
                "dimension_2": _clean_dimension(row.get(dimensions[1])) if len(dimensions) > 1 else "",
                "metrics": _json_safe_metrics(row),
            }
        )
    return result


def _metrics_from_row(row: Any) -> dict[str, Any]:
    return {
        "total_count": _int_value(_row_get(row, COUNT_COL)),
        "missed_count": _int_value(_row_get(row, MISSED_COUNT_COL)),
        "missed_rate": _float_value(_row_get(row, MISSED_RATE_COL)),
        "quality_count": _int_value(_row_get(row, QUALITY_COUNT_COL)),
        "quality_rate": _float_value(_row_get(row, QUALITY_RATE_COL)),
        "demand_count": _int_value(_row_get(row, DEMAND_COUNT_COL)),
        "demand_rate": _float_value(_row_get(row, DEMAND_RATE_COL)),
    }


def _json_safe_metrics(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        items = row.items()
    else:
        items = row.to_dict().items()
    result = {}
    for key, value in items:
        if pd.isna(value):
            result[str(key)] = None
        elif hasattr(value, "item"):
            result[str(key)] = value.item()
        else:
            result[str(key)] = value
    return result


def _export_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_code": row["project_code"],
        "export_number": row["export_number"],
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "analysis_date": row["analysis_date"],
        "source_file_name": row["source_file_name"],
        "report_file_name": row["report_file_name"],
        "run_id": row["run_id"],
        "settings": json.loads(row["settings_json"] or "{}"),
        "total_count": row["total_count"],
        "missed_count": row["missed_count"],
        "missed_rate": row["missed_rate"],
        "quality_count": row["quality_count"],
        "quality_rate": row["quality_rate"],
        "demand_count": row["demand_count"],
        "demand_rate": row["demand_rate"],
    }


def _period_row(row: Any) -> dict[str, Any]:
    return {
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "total_count": row["total_count"],
        "missed_count": row["missed_count"],
        "missed_rate": row["missed_rate"],
        "quality_count": row["quality_count"],
        "quality_rate": row["quality_rate"],
        "demand_count": row["demand_count"],
        "demand_rate": row["demand_rate"],
    }


def _legacy_period(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item[key] for key in (
        "period_start",
        "period_end",
        "total_count",
        "missed_count",
        "missed_rate",
        "quality_count",
        "quality_rate",
        "demand_count",
        "demand_rate",
    )}


def _flatten_exports(exports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {**item, **period}
        for item in exports
        for period in item.get("periods", [_legacy_period(item)])
    ]


def _row_get(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return row.get(key)


def _clean_dimension(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value)


def _int_value(value: Any) -> int:
    if value is None or pd.isna(value):
        return 0
    return int(value)


def _float_value(value: Any) -> float:
    if value is None or pd.isna(value):
        return 0.0
    return float(value)


def _pp(value: float) -> float:
    return round(value * 100, 2)


def _brief_export_conclusion(item: dict[str, Any], previous: dict[str, Any] | None) -> str:
    if previous is None:
        return "Базовая выгрузка для сравнения."
    return _comparison_comment(item, previous)


def _comparison_comment(item: dict[str, Any], previous: dict[str, Any] | None) -> str:
    if previous is None:
        return ""
    parts = []
    demand_delta = item["demand_rate"] - previous["demand_rate"]
    quality_delta = item["quality_rate"] - previous["quality_rate"]
    missed_delta = item["missed_rate"] - previous["missed_rate"]
    if demand_delta > 0:
        parts.append("сигнал спроса % вырос, эффективность лучше")
    elif demand_delta < 0:
        parts.append("сигнал спроса % снизился, эффективность хуже")
    if quality_delta > 0:
        parts.append("качественные % выросли")
    elif quality_delta < 0:
        parts.append("качественные % снизились")
    if missed_delta > 0:
        parts.append("недозвон % вырос, дозвон хуже")
    elif missed_delta < 0:
        parts.append("недозвон % снизился")
    if item["total_count"] > previous["total_count"] and demand_delta < 0:
        parts.append("объем больше, но эффективность ниже")
    if item["total_count"] < SMALL_SAMPLE_THRESHOLD:
        parts.append("маленький объем, вывод осторожный")
    return "; ".join(parts) or "существенной процентной динамики нет"
