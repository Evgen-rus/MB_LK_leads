import pandas as pd

from backend.app.lead_analytics.matcher import _build_index
from backend.app.lead_analytics.source_utils import extract_lkid_from_source


def test_extract_lkid_after_last_underscore():
    assert extract_lkid_from_source("baltlease.ru_300123") == ""
    assert extract_lkid_from_source("alfaleasing.ru_SMS_30169258") == "30169258"
    assert extract_lkid_from_source("alfaleasing.ru_78003024486") == ""
    assert extract_lkid_from_source("no_lkid_here") == ""
    assert extract_lkid_from_source("example.ru_заявка_79001234567_30222737") == "30222737"
    assert extract_lkid_from_source("example.ru_заявка_30222738") == "30222738"
    assert extract_lkid_from_source("example.ru_78005501234") == ""
    assert extract_lkid_from_source("example.ru_88005501234") == ""
    assert extract_lkid_from_source("example.ru_8005501234") == ""
    assert extract_lkid_from_source("example.ru_заявка_79001234567") == ""
    assert extract_lkid_from_source("example.ru") == ""
    assert extract_lkid_from_source("example.ru_any_78005501234_79001234567_30222737") == "30222737"
    assert extract_lkid_from_source("example.ru_20222737") == ""
    assert extract_lkid_from_source("example.ru_302227370") == ""


def test_build_index_uses_latest_date_then_latest_row():
    client = pd.DataFrame(
        {
            "_lkid": ["dated", "dated", "without-date", "without-date"],
            "_date": pd.to_datetime(["2026-01-01", "2026-02-01", None, None]),
            "_row": [1, 2, 3, 4],
            "value": ["old", "new", "first", "last"],
        }
    )

    index = _build_index(client, "_lkid")

    assert index["dated"]["value"] == "new"
    assert index["without-date"]["value"] == "last"


def test_ambiguity_counts_only_used_key_and_preserves_lk_rows(tmp_path):
    from backend.app.lead_analytics.matcher import match_files
    from backend.app.lead_analytics.models import ColumnMapping
    lk = pd.DataFrame({"id": [30222737, 30222738], "phone": ["79001234567", "79001234567"]})
    client = pd.DataFrame({"id": [30222737, 30222737, 30222738], "phone": ["79001234567"] * 3,
                           "status": ["old", "new", "unique"], "date": ["2026-01-01", "2026-02-01", "2026-01-01"]})
    lk.to_excel(tmp_path / "lk.xlsx", index=False)
    client.to_excel(tmp_path / "client.xlsx", index=False)
    path = match_files("test", tmp_path / "lk.xlsx", tmp_path / "client.xlsx",
                       ColumnMapping("Sheet1", lkid_column="id", phone_column="phone"),
                       ColumnMapping("Sheet1", lkid_column="id", phone_column="phone", status_column="status", date_column="date"), tmp_path)
    sheets = pd.read_excel(path, sheet_name=None)
    assert sheets["Сопоставленные"]["Статус клиента"].tolist() == ["new", "unique"]
    check = sheets["Проверка"].set_index("Показатель")["Значение"]
    assert check["Неоднозначные сопоставления"] == 1
    details = sheets["Неоднозначные сопоставления"]
    assert details["Тип ключа"].tolist() == ["LKID", "LKID"]
    assert details["Выбрана строка"].tolist() == ["нет", "да"]
    assert "Как читать сопоставление" in sheets
    assert sheets["Сопоставленные"]["Неоднозначное сопоставление"].tolist() == ["да", "нет"]
