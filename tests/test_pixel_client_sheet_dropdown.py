"""Pixel dropdown requests use C2 and clear values only in newly added rows."""

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tool_export_pixel_leads_to_client_sheets as export


def test_new_rows_copy_complete_template_and_clear_selected_status(monkeypatch):
    service = MagicMock()
    monkeypatch.setattr(export, "_safe_google_call", lambda call: call())

    export._copy_dropdown_validation_to_rows(service, "test-sheet", 10, 354, 370)

    requests = service.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]["requests"]
    copy = requests[0]["copyPaste"]
    assert copy["pasteType"] == "PASTE_NORMAL"
    assert copy["source"] == {
        "sheetId": 10, "startRowIndex": 1, "endRowIndex": 2,
        "startColumnIndex": 2, "endColumnIndex": 3,
    }
    assert copy["destination"] == {
        "sheetId": 10, "startRowIndex": 353, "endRowIndex": 370,
        "startColumnIndex": 2, "endColumnIndex": 3,
    }
    assert requests[1] == {"repeatCell": {
        "range": copy["destination"], "cell": {}, "fields": "userEnteredValue",
    }}


def test_new_month_copies_c2_from_previous_month_without_its_value(monkeypatch):
    service = MagicMock()
    monkeypatch.setattr(export, "_safe_google_call", lambda call: call())
    monkeypatch.setattr(export, "_sheet_props_by_title", lambda *args: {
        "Октябрь 2026": {"sheetId": 10, "index": 0},
    })
    service.spreadsheets.return_value.batchUpdate.return_value.execute.return_value = {
        "replies": [{"addSheet": {"properties": {"sheetId": 11}}}],
    }

    export._ensure_month_sheet(service, "test-sheet", "Ноябрь 2026", datetime(2026, 11, 1))

    calls = service.spreadsheets.return_value.batchUpdate.call_args_list
    requests = calls[-1].kwargs["body"]["requests"]
    assert requests[0]["copyPaste"]["source"]["sheetId"] == 10
    assert requests[0]["copyPaste"]["source"]["startRowIndex"] == 1
    assert requests[0]["copyPaste"]["destination"]["sheetId"] == 11
    assert requests[0]["copyPaste"]["destination"]["startRowIndex"] == 1
    assert requests[0]["copyPaste"]["destination"]["endRowIndex"] == 2
    assert requests[1]["repeatCell"]["fields"] == "userEnteredValue"


def test_existing_month_is_not_modified(monkeypatch):
    service = MagicMock()
    props = {"sheetId": 10}
    monkeypatch.setattr(export, "_sheet_props_by_title", lambda *args: {"Октябрь 2026": props})

    assert export._ensure_month_sheet(
        service, "test-sheet", "Октябрь 2026", datetime(2026, 10, 16),
    ) == props
    service.spreadsheets.assert_not_called()


def test_export_copies_dropdown_only_to_appended_rows(monkeypatch):
    service = MagicMock()
    timestamp = datetime(2026, 10, 16, 10)
    rows = [export.PixelLeadExportRow(
        id=lead_id, phone="70000000000", project_name="test.example",
        prov_created_at=timestamp, imported_at=timestamp, pixel_url="https://test.example",
        client_id=2, pixel_table_url="https://docs.google.com/spreadsheets/d/test-sheet",
    ) for lead_id in (1, 2)]
    engine = MagicMock()
    monkeypatch.setattr(export, "_get_env", lambda *args: "1000" if args[0].endswith("BATCH_LIMIT") else "test")
    monkeypatch.setattr(export.os.path, "exists", lambda *args: True)
    monkeypatch.setattr(export, "_build_sheets_client", lambda *args: service)
    monkeypatch.setattr(export.db, "init_engine_and_session", lambda *args: (engine, None))
    monkeypatch.setattr(export.models.Base.metadata, "create_all", lambda **kwargs: None)
    monkeypatch.setattr(export, "_ensure_db_columns", lambda *args: None)
    monkeypatch.setattr(export, "_fetch_pending_rows", lambda *args, **kwargs: rows)
    monkeypatch.setattr(export, "_count_pending_without_table", lambda *args: 0)
    monkeypatch.setattr(export, "_ensure_month_sheet", lambda *args: {"sheetId": 10})
    monkeypatch.setattr(export, "_get_existing_values_count", lambda *args: 353)
    monkeypatch.setattr(export, "_get_existing_lk_ids", lambda *args: {rows[0].lk_id})
    monkeypatch.setattr(export, "_ensure_rows", lambda *args: None)
    monkeypatch.setattr(export, "_mark_exported", lambda _, ids: len(ids))
    monkeypatch.setattr(export, "_safe_google_call", lambda call: call())
    copy_dropdown = MagicMock()
    monkeypatch.setattr(export, "_copy_dropdown_validation_to_rows", copy_dropdown)
    service.spreadsheets.return_value.values.return_value.append.return_value.execute.return_value = {
        "updates": {"updatedRange": "'Октябрь 2026'!A354:U354"},
    }

    export.export_pixel_leads_to_client_sheets()

    copy_dropdown.assert_called_once_with(service, "test-sheet", 10, 354, 354)
    values = service.spreadsheets.return_value.values.return_value.append.call_args.kwargs["body"]["values"]
    assert len(values) == 1
    assert values[0][2] == ""
    assert values[0][17] == rows[1].lk_id
