import pandas as pd

from backend.app.lead_analytics.config import MATCHED_SHEET_NAME
from backend.app.lead_analytics.models import ColumnMapping
from backend.app.lead_analytics.structure_detector import choose_analyze_sheet, detect_analyze_mapping, detect_match_mapping, prepare_analyze_mapping


def test_detect_analyze_mapping_from_excel(tmp_path):
    path = tmp_path / "sample.xlsx"
    pd.DataFrame(
        {
            "Дата": ["2026-01-01"],
            "Номера": ["79231234567"],
            "Канал": ["A"],
            "Источники": ["site.ru_12345"],
            "Стадия сделки": ["Недозвон"],
        }
    ).to_excel(path, index=False)
    mapping = detect_analyze_mapping(path)
    assert mapping.date_column == "Дата"
    assert mapping.phone_column == "Номера"
    assert mapping.channel_column == "Канал"
    assert mapping.source_column == "Источники"
    assert mapping.status_column == "Стадия сделки"


def test_detect_match_mapping_recognizes_phone_numbers_column(tmp_path):
    path = tmp_path / "match.xlsx"
    pd.DataFrame({"Номера": ["79231234567"], "Статус": ["Новый"]}).to_excel(path, index=False)
    assert detect_match_mapping(path, "client").phone_column == "Номера"


def test_detect_analyze_mapping_prefers_matched_sheet(tmp_path):
    path = tmp_path / "match.xlsx"
    matched = pd.DataFrame(
        {
            "Дата": ["2026-01-01"],
            "Телефон": ["79231234567"],
            "Канал": ["A"],
            "Источники": ["site.ru_12345"],
            "Статус клиента": ["Недозвон"],
            "Комментарий клиента": [""],
        }
    )
    duplicates = pd.DataFrame(
        {
            "Дата создания": ["2026-01-01"] * 3,
            "Рабочий телефон": ["79001112233"] * 3,
            "Стадия": ["СПАМ"] * 3,
            "_status": ["СПАМ"] * 3,
            "_comment": [""] * 3,
            "Источник": ["ЛидгенБюро DMP"] * 3,
        }
    )
    with pd.ExcelWriter(path) as writer:
        matched.to_excel(writer, sheet_name=MATCHED_SHEET_NAME, index=False)
        duplicates.to_excel(writer, sheet_name="Дубли клиента", index=False)

    mapping = detect_analyze_mapping(path, sheet_name="Дубли клиента")
    assert choose_analyze_sheet(path) == MATCHED_SHEET_NAME
    assert mapping.sheet_name == MATCHED_SHEET_NAME
    assert mapping.status_column == "Статус клиента"
    assert mapping.date_column == "Дата"
    assert mapping.phone_column == "Телефон"
    assert mapping.comment_column == "Комментарий клиента"

    stale = ColumnMapping(
        sheet_name="Дубли клиента",
        date_column="Дата создания",
        phone_column="Рабочий телефон",
        status_column="_status",
        comment_column="_comment",
        source_column="Источник",
    )
    prepared = prepare_analyze_mapping(path, stale)
    assert prepared.sheet_name == MATCHED_SHEET_NAME
    assert prepared.status_column == "Статус клиента"
