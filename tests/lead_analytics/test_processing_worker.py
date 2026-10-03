import pandas as pd

from backend.app.lead_analytics import db
from backend.app.lead_analytics import router


def test_match_job_reports_row_progress_and_writes_report(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "analytics.db")
    monkeypatch.setattr(router, "RUNS_DIR", tmp_path / "runs")
    db.init_db()
    group_id = db.create_group(9, "Demo", [4], None)
    run_id = "test-run"
    input_dir = router.RUNS_DIR / run_id / "input"
    input_dir.mkdir(parents=True)
    pd.DataFrame(
        {"LKID": ["1", "2"], "Источник": ["site_1", "site_2"], "Дата": ["2026-01-01", "2026-01-02"]}
    ).to_excel(input_dir / "lk.xlsx", index=False)
    pd.DataFrame(
        {"Статус": ["Заявка принята", "Недозвон"], "LKID": ["1", "2"], "Дата": ["2026-01-01", "2026-01-02"]}
    ).to_excel(input_dir / "client.xlsx", index=False)

    db.create_run({"id": run_id, "group_id": group_id, "client_id": 9, "group_name": "Demo",
                   "project_ids": [4], "project_names": {"4": "Source"}, "periods": [],
                   "lk_snapshot": "test/lk.xlsx", "client_snapshot": "test/client.xlsx",
                   "source_file_name": "client.xlsx"})
    job = db.create_processing_job(run_id, "match", {
        "project": "Demo",
        "lk_mapping": {"sheet_name": "Sheet1", "lkid_column": "LKID", "source_column": "Источник", "date_column": "Дата"},
        "client_mapping": {"sheet_name": "Sheet1", "lkid_column": "LKID", "status_column": "Статус", "date_column": "Дата"},
    }, group_id=group_id)
    router._run_job(job)
    current = db.get_processing_job(int(job["id"]))

    assert current["status"] == "completed"
    assert current["processed_rows"] == 4
    assert current["total_rows"] == 4
    assert (router.RUNS_DIR / run_id / "output" / current["output_file_name"]).exists()
