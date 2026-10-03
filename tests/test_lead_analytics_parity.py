"""Compare both pipelines on synthetic data without touching the old storage."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = Path(os.environ.get("LEAD_ANALYTICS_SOURCE", ROOT.parent / "Lead_analytics"))

# Each subprocess imports its own package and redirects every storage path before
# importing the pipeline. Original source and original user data remain read-only.
SCRIPT = r'''
import importlib
from pathlib import Path
import sys
import pandas as pd

root, target, prefix = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, str(root))
config = importlib.import_module(prefix + '.config')
config.DATA_DIR = target
config.INPUT_DIR = target / 'input'
config.OUTPUT_DIR = target / 'output'
config.RUNS_DIR = target / 'runs'
config.ANALYSIS_REPORTS_DIR = target / 'reports'
config.RULES_DIR = target / 'rules'
config.DB_PATH = target / 'analytics.db'
for folder in [target, config.INPUT_DIR, config.OUTPUT_DIR, config.RUNS_DIR,
               config.ANALYSIS_REPORTS_DIR, config.RULES_DIR]:
    folder.mkdir(parents=True, exist_ok=True)
db = importlib.import_module(prefix + '.db')
db.DB_PATH = config.DB_PATH
models = importlib.import_module(prefix + '.models')
matcher = importlib.import_module(prefix + '.matcher')
pipeline = importlib.import_module(prefix + '.pipeline')
history = importlib.import_module(prefix + '.export_history')
pipeline.ANALYSIS_REPORTS_DIR = config.ANALYSIS_REPORTS_DIR
history.ANALYSIS_REPORTS_DIR = config.ANALYSIS_REPORTS_DIR
db.init_db()
project = 'Synthetic group'
for status, group in [('S1', 'Качественные'), ('S2', 'Недозвон'),
                      ('S3', 'Некачественные'), ('S4', 'Рабочий потенциал')]:
    db.add_status_rule(models.StatusRule(pattern=status, match_type='exact',
        group_name=group, project_code=project, priority=10))
lk = target / 'input' / 'lk.xlsx'
client = target / 'input' / 'client.xlsx'
pd.DataFrame({
    'Дата': ['2026-01-01', '2026-01-07', '2026-01-08', '2026-01-14', '2026-01-15'],
    'Телефон': ['70000000001', '70000000002', '70000000003', '70000000004', '70000000005'],
    'Канал': ['A', 'B', 'A', 'C', 'D'],
    'Источники': ['one.example', 'two.example', 'one.example', 'three.example', 'four.example'],
    'Проект': ['First', 'Second', 'First', 'Third', 'Removed'],
    'lk id': ['101', '102', '103', '104', '105'],
}).to_excel(lk, index=False)
pd.DataFrame({
    'Дата': ['2026-01-01', '2026-01-07', '2026-01-08', '2026-01-14', '2026-01-15'],
    'Телефон': ['70000000001', '70000000002', '70000000003', '70000000004', '70000000005'],
    'LKID': ['101', '102', '103', '104', '105'],
    'Статус': ['S1', 'S2', 'S3', 'S4', None],
    'Комментарий': ['a', 'b', '', 'd', ''],
}).to_excel(client, index=False)
matched = matcher.match_files(project, lk, client,
    models.ColumnMapping(sheet_name='Sheet1', date_column='Дата', phone_column='Телефон',
        source_column='Источники', lkid_column='lk id', project_column='Проект'),
    models.ColumnMapping(sheet_name='Sheet1', date_column='Дата', phone_column='Телефон',
        lkid_column='LKID', status_column='Статус', comment_column='Комментарий'),
    config.OUTPUT_DIR)
detector = importlib.import_module(prefix + '.structure_detector')
pipeline.analyze_file(project, matched, detector.prepare_analyze_mapping(matched),
    config.OUTPUT_DIR, history.ExportMetadata(export_number=None,
        periods=[history.ExportPeriod('2026-01-01', '2026-01-15'),
                 history.ExportPeriod('2026-01-01', '2026-01-07'),
                 history.ExportPeriod('2026-01-08', '2026-01-14')],
        analysis_date='2026-01-16', source_file_name='synthetic.xlsx'))
'''


def workbook_contents(path):
    book = load_workbook(path, data_only=False)
    try:
        source_root = str(path.parent.parent)
        return {
            sheet.title: [
                tuple(value.replace(source_root, "<storage>") if isinstance(value, str) else value for value in row)
                for row in sheet.values
            ]
            for sheet in book.worksheets
        }
    finally:
        book.close()


@pytest.mark.skipif(not (ORIGINAL / "app/pipeline.py").is_file(), reason="Original source unavailable; set LEAD_ANALYTICS_SOURCE")
def test_original_and_integrated_workbooks_have_same_contents(tmp_path):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    for source, output, prefix in [
        (ORIGINAL, tmp_path / "original", "app"),
        (ROOT, tmp_path / "integrated", "backend.app.lead_analytics"),
    ]:
        result = subprocess.run(
            [sys.executable, "-c", SCRIPT, str(source), str(output), prefix],
            cwd=tmp_path, env={**env, "LEAD_ANALYTICS_DATA_DIR": str(output)},
            capture_output=True, text=True, encoding="utf-8", timeout=90,
        )
        assert result.returncode == 0, result.stderr
    original_files = {p.name: p for p in (tmp_path / "original/output").glob("*.xlsx")}
    integrated_files = {p.name: p for p in (tmp_path / "integrated/output").glob("*.xlsx")}
    assert original_files.keys() == integrated_files.keys()
    assert len(original_files) == 2
    for name in original_files:
        assert workbook_contents(original_files[name]) == workbook_contents(integrated_files[name]), name
