from pathlib import Path

import pandas as pd

from backend.app.lead_analytics import db, export_history
from backend.app.lead_analytics.export_history import (
    ExportMetadata,
    ExportPeriod,
    analysis_report_path,
    build_conclusions,
    build_export_dynamics,
    build_registry,
    delete_analysis_export,
    list_exports,
    save_analysis_export,
)


def test_registry_conclusion_prefers_percent_over_volume():
    exports = [
        {
            "export_number": 1,
            "period_start": "2026-06-01",
            "period_end": "2026-06-07",
            "analysis_date": "2026-06-08",
            "source_file_name": "first.xlsx",
            "total_count": 100,
            "missed_count": 20,
            "missed_rate": 0.2,
            "quality_count": 10,
            "quality_rate": 0.1,
            "demand_count": 10,
            "demand_rate": 0.1,
        },
        {
            "export_number": 2,
            "period_start": "2026-06-01",
            "period_end": "2026-06-29",
            "analysis_date": "2026-06-30",
            "source_file_name": "second.xlsx",
            "total_count": 500,
            "missed_count": 150,
            "missed_rate": 0.3,
            "quality_count": 30,
            "quality_rate": 0.06,
            "demand_count": 30,
            "demand_rate": 0.06,
        },
    ]

    registry = build_registry(exports)
    dynamics = build_export_dynamics(exports)
    conclusions = build_conclusions(exports, "Demo")

    assert "объем больше, но эффективность ниже" in registry.loc[1, "Краткий вывод"]
    assert dynamics.loc[1, "Изм. качественных, п.п."] == -4
    assert dynamics.loc[1, "Изм. сигнала спроса, п.п."] == -4
    assert any("Объем вырос, эффективность снизилась" in item for item in conclusions)


def test_export_number_is_automatic_and_periods_are_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "analytics.db")
    db.init_db()
    total = pd.DataFrame(
        {
            "Всего идентификаций": [2],
            "Недозвон": [1],
            "Недозвон %": [0.5],
            "Качественные": [1],
            "Кач. %": [0.5],
            "Сигнал спроса": [1],
            "Сигнал спроса %": [0.5],
        }
    )
    periods = [ExportPeriod("2026-01-01", "2026-01-31"), ExportPeriod("2026-02-01", "2026-02-28")]
    results = [(period, total, {"domain_channel": pd.DataFrame(), "source_channel": pd.DataFrame(), "channel": pd.DataFrame()}) for period in periods]

    save_analysis_export("Demo", ExportMetadata(None, periods), results)
    save_analysis_export("Demo", ExportMetadata(None, periods[:1]), results[:1])

    exports = list_exports("Demo")
    assert [item["export_number"] for item in exports] == [1, 2]
    assert [(item["period_start"], item["period_end"]) for item in exports[0]["periods"]] == [
        ("2026-01-01", "2026-01-31"),
        ("2026-02-01", "2026-02-28"),
    ]


def test_saved_reports_are_independent_and_deleted_by_export(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "analytics.db")
    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(export_history, "ANALYSIS_REPORTS_DIR", reports_dir)
    db.init_db()
    total = pd.DataFrame(
        {
            "Всего идентификаций": [2],
            "Недозвон": [1],
            "Недозвон %": [0.5],
            "Качественные": [1],
            "Кач. %": [0.5],
            "Сигнал спроса": [1],
            "Сигнал спроса %": [0.5],
        }
    )
    period = ExportPeriod("2026-01-01", "2026-01-31")
    report_name = "0123456789abcdef0123456789abcdef.xlsx"
    report_path = reports_dir / report_name
    reports_dir.mkdir(parents=True)
    report_path.write_bytes(b"complete workbook")
    export_id = save_analysis_export(
        "Demo",
        ExportMetadata(None, [period]),
        [(period, total, {"domain_channel": pd.DataFrame(), "source_channel": pd.DataFrame(), "channel": pd.DataFrame()})],
        report_file_name=report_name,
        run_id="frozen-run",
        settings_snapshot={"status_rules": [{"pattern": "new", "group_name": "Качественные"}]},
    )

    history = list_exports("Demo")
    assert history[0]["id"] == export_id
    assert Path(analysis_report_path(history[0]["report_file_name"])) == report_path
    assert history[0]["run_id"] == "frozen-run"
    assert history[0]["settings"]["status_rules"][0]["pattern"] == "new"
    second_id = save_analysis_export(
        "Demo",
        ExportMetadata(None, [period]),
        [(period, total, {"domain_channel": pd.DataFrame(), "source_channel": pd.DataFrame(), "channel": pd.DataFrame()})],
        report_file_name="fedcba9876543210fedcba9876543210.xlsx",
    )
    assert [item["id"] for item in list_exports("Demo")] == [export_id, second_id]
    assert delete_analysis_export("Demo", 1)
    assert not report_path.exists()
    assert len(list_exports("Demo")) == 1
