import pytest
from fastapi import HTTPException

from datetime import date

from backend.app.lead_analytics.router import AnalysisPeriodPayload, _unknown_status_counts, _validate_periods, _validate_unknown_status_rules


def test_unknown_status_rules_require_a_group_for_every_unknown_status():
    with pytest.raises(HTTPException, match="Выберите группу"):
        _validate_unknown_status_rules(["Новый статус", "Другой статус"], {"Новый статус": "Качественные"})


def test_unknown_status_rules_accept_complete_valid_assignment():
    _validate_unknown_status_rules(
        ["Новый статус", "Другой статус"],
        {"Новый статус": "Качественные", "Другой статус": "Недозвон"},
    )


def test_unknown_status_rules_reject_unknown_group():
    with pytest.raises(HTTPException, match="Неизвестная группа"):
        _validate_unknown_status_rules(["Новый статус"], {"Новый статус": "Другая группа"})


def test_periods_are_required_and_ordered():
    with pytest.raises(HTTPException, match="хотя бы один период"):
        _validate_periods([])
    with pytest.raises(HTTPException, match="не может быть позже"):
        _validate_periods([AnalysisPeriodPayload(period_start=date(2026, 2, 1), period_end=date(2026, 1, 1))])


def test_unknown_status_counts_use_full_values_and_ignore_blanks():
    assert _unknown_status_counts(["Новый", " Новый ", None, "", "Другой"], ["Новый"]) == {"Новый": 2}
