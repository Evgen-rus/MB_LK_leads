from backend.app.lead_analytics import status_classifier
from backend.app.lead_analytics import db
from backend.app.lead_analytics.status_classifier import classify, sorted_rules, unknown_statuses
from backend.app.lead_analytics.models import StatusRule


def test_unconfigured_statuses_need_manual_mapping(monkeypatch):
    assert classify("Заявка принята", project="x")[0] == "Требует проверки"
    assert classify("Недозвон", project="x")[0] == "Требует проверки"
    assert classify("client@example.com", project="x")[0] == "Требует проверки"


def test_classification_ignores_comments():
    assert classify("Недозвон", "Работает с конкурентом", project="x")[0] == "Требует проверки"
    assert classify("Новый статус", "client@example.com", project="x")[0] == "Требует проверки"


def test_missing_statuses_are_not_counted():
    assert classify(None, project="x")[0] == "Не учитывать"
    assert classify("", project="x")[0] == "Не учитывать"
    assert classify(float("nan"), project="x")[0] == "Не учитывать"
    assert classify("nan", project="x")[0] == "Не учитывать"
    assert unknown_statuses([None, "", float("nan"), "nan", "совсем новый статус"], "x") == ["совсем новый статус"]


def test_classify_uses_preloaded_rules_without_reloading(monkeypatch):
    rules = sorted_rules("x")

    def unexpected_reload(project):
        raise AssertionError(f"Правила не должны загружаться повторно для {project}")

    monkeypatch.setattr(status_classifier, "sorted_rules", unexpected_reload)

    assert classify("Заявка принята", project="x", rules=rules)[0] == "Требует проверки"


def test_global_legacy_rules_do_not_apply_to_groups(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "analytics.db")
    db.init_db()
    db.add_status_rule(StatusRule(pattern="Shared", match_type="exact", group_name="Качественные"))
    db.add_status_rule(StatusRule(pattern="Local", match_type="exact", group_name="Недозвон", project_code="group:1"))

    rules = sorted_rules("group:1")

    assert [rule.pattern for rule in rules] == ["Local"]
    assert sorted_rules("group:2") == []
