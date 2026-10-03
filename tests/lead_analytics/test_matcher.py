import pandas as pd

from backend.app.lead_analytics.matcher import _build_index
from backend.app.lead_analytics.source_utils import extract_lkid_from_source


def test_extract_lkid_after_last_underscore():
    assert extract_lkid_from_source("baltlease.ru_300123") == "300123"
    assert extract_lkid_from_source("alfaleasing.ru_SMS_30169258") == "30169258"
    assert extract_lkid_from_source("alfaleasing.ru_78003024486") == "78003024486"
    assert extract_lkid_from_source("no_lkid_here") == ""


def test_build_index_uses_latest_date_then_latest_row_and_marks_duplicates():
    client = pd.DataFrame(
        {
            "_lkid": ["dated", "dated", "without-date", "without-date"],
            "_date": pd.to_datetime(["2026-01-01", "2026-02-01", None, None]),
            "_row": [1, 2, 3, 4],
            "value": ["old", "new", "first", "last"],
        }
    )

    index, duplicates = _build_index(client, "_lkid", "LKID")

    assert index["dated"]["value"] == "new"
    assert index["without-date"]["value"] == "last"
    assert [(row["Ключ"], row["Выбрана строка"], row["Причина выбора"]) for row in duplicates] == [
        ("dated", "нет", "последняя дата"),
        ("dated", "да", "последняя дата"),
        ("without-date", "нет", "последняя строка"),
        ("without-date", "да", "последняя строка"),
    ]
