import os
from pathlib import Path


DATA_DIR = Path(os.getenv("LEAD_ANALYTICS_DATA_DIR", Path(__file__).resolve().parents[3] / "data" / "lead_analytics")).resolve()
RUNS_DIR = DATA_DIR / "runs"
ANALYSIS_REPORTS_DIR = DATA_DIR / "analysis-reports"
DB_PATH = DATA_DIR / "analytics.db"
MATCHED_SHEET_NAME = "Сопоставленные"


def ensure_dirs() -> None:
    for path in (DATA_DIR, RUNS_DIR, ANALYSIS_REPORTS_DIR):
        path.mkdir(parents=True, exist_ok=True)
