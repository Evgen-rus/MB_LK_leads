"""
Проверки бизнес-логики «Лимита группы проектов на день».

Запуск (репозиторий не имеет настроенного test runner, поэтому pytest
используется напрямую из окружения):

    python -m pytest tests/test_daily_export_limit_groups.py -q

Тесты работают на временной SQLite-БД и не трогают реальные данные.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SHEETS_TZ", "Europe/Moscow")

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from backend.app import daily_export_limit_groups as limit_groups  # noqa: E402
from backend.app import models  # noqa: E402
from backend.app.time_utils import as_local_naive, now_msk  # noqa: E402


# -------------------------------------------------------------------
# Фикстуры
# -------------------------------------------------------------------

@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(bind=engine)
    LocalSession = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
    with LocalSession() as s:
        yield s
    engine.dispose()


def _make_client(s, login: str = "client1") -> models.User:
    user = models.User(
        id=2,
        login=login,
        password_hash="x",
        display_name="Клиент Один",
        role="client",
        created_at=now_msk(),
    )
    s.add(user)
    s.add(models.ClientProfile(user_id=2, name="Клиент Один", created_at=now_msk(), updated_at=now_msk()))
    s.commit()
    return user


def _make_project(s, project_id: int, name: str, *, user_id: int = 2, source: str = "B1") -> models.Project:
    project = models.Project(
        id=project_id,
        user_id=user_id,
        name=name,
        tag="tag",
        collection_source="Сайты",
        data_source_code=source,
        status="Активен",
        delivery_status="Активна",
        data_limit=0,
        numbers_today=0,
        numbers_total=0,
        days_received="",
        sources_count=0,
        created_at=now_msk(),
        updated_at=now_msk(),
    )
    s.add(project)
    s.commit()
    return project


def _make_lead(
    s,
    lead_id: int,
    project_id: int,
    *,
    when: datetime,
    exported_at: datetime | None = None,
    lead_source: str = "provider",
) -> models.ProviderLead:
    lead = models.ProviderLead(
        id=lead_id,
        vid=f"vid-{lead_id}",
        lead_source=lead_source,
        project_name=f"B1_{project_id}",
        prov_created_at=when,
        imported_at=when,
        project_id=project_id,
        provider_sheet_exported_at=exported_at,
    )
    s.add(lead)
    s.commit()
    return lead


def _at(day: date, hour: int = 10) -> datetime:
    return as_local_naive(datetime(day.year, day.month, day.day, hour, 0, 0, tzinfo=now_msk().tzinfo))


DAY1 = date(2026, 3, 10)
DAY2 = date(2026, 3, 11)


def _candidate_ids(s, project_ids, *, exported: bool = False) -> list[int]:
    """
    Кандидаты текущей пачки — те, что вернул бы экспорт:
    provider-лиды этих проектов с NULL-меткой, в FIFO-порядке.
    """
    stmt = (
        select(models.ProviderLead.id)
        .where(models.ProviderLead.lead_source == "provider")
        .where(models.ProviderLead.project_id.in_([int(p) for p in project_ids]))
        .where(models.ProviderLead.provider_sheet_exported_at.is_(None))
        .order_by(models.ProviderLead.imported_at.asc(), models.ProviderLead.id.asc())
    )
    return [int(value) for value in s.execute(stmt).scalars().all()]


def _plan(s, project_ids, day=DAY1):
    """
    План ровно так, как его строит экспорт: по кандидатам пачки, а не по всем
    NULL-лидам проектов.  Параметр project_ids оставлен для читаемости сценариев.
    """
    return limit_groups.build_export_plan(s, lead_ids=_candidate_ids(s, project_ids), day=day)


# -------------------------------------------------------------------
# 1. 600 лидов, лимит 500 -> 500 export, 100 pending
# -------------------------------------------------------------------

def test_day1_600_leads_limit_500(session):
    _make_client(session)
    _make_project(session, 10, "B1_[LR223] ПрактикМ")
    group = limit_groups.create_group(
        session, client_id=2, name="ПрактикМ", daily_limit=500, project_ids=[10]
    )

    for index in range(600):
        _make_lead(session, 1000 + index, 10, when=_at(DAY1, 8) + timedelta(minutes=index))

    quota = limit_groups.get_group_quota(session, int(group.id), day=DAY1)
    assert quota.pending_total == 600
    assert quota.exported_today == 0
    assert quota.remaining_today == 500

    plan = _plan(session, [10], day=DAY1)
    assert plan.total == 500

    # Помечаем выгруженные и проверяем остаток очереди.
    _mark_exported(session, plan.lead_ids(), when=_at(DAY1, 18))
    quota_after = limit_groups.get_group_quota(session, int(group.id), day=DAY1)
    assert quota_after.exported_today == 500
    assert quota_after.pending_total == 100
    assert quota_after.limit_reached is True
    assert quota_after.remaining_today == 0


# -------------------------------------------------------------------
# 2. Следующий день +300 -> экспорт 100 старых +300 новых
# -------------------------------------------------------------------

def test_day2_old_pending_first_then_new(session):
    _make_client(session)
    _make_project(session, 10, "B1_[LR223] ПрактикМ")
    group = limit_groups.create_group(
        session, client_id=2, name="ПрактикМ", daily_limit=500, project_ids=[10]
    )

    for index in range(600):
        _make_lead(session, 1000 + index, 10, when=_at(DAY1, 8) + timedelta(minutes=index))
    _mark_exported(session, _plan(session, [10], day=DAY1).lead_ids(), when=_at(DAY1, 18))
    for index in range(300):
        _make_lead(session, 2000 + index, 10, when=_at(DAY2, 8) + timedelta(minutes=index))

    quota = limit_groups.get_group_quota(session, int(group.id), day=DAY2)
    assert quota.exported_today == 0
    assert quota.pending_total == 400  # 100 старых + 300 новых
    assert quota.remaining_today == 500

    plan = _plan(session, [10], day=DAY2)
    assert plan.total == 400

    # FIFO: сначала 100 самых старых (pending с дня 1), затем новые.
    ids = plan.lead_ids()
    assert ids[:100] == list(range(1500, 1600))
    assert ids[100:] == list(range(2000, 2300))

    _mark_exported(session, ids, when=_at(DAY2, 18))
    quota_after = limit_groups.get_group_quota(session, int(group.id), day=DAY2)
    assert quota_after.exported_today == 400
    assert quota_after.pending_total == 0


# -------------------------------------------------------------------
# 3. Остаток 200 +600 новых -> ровно 500 export, 300 pending
# -------------------------------------------------------------------

def test_day2_backlog_200_plus_600_new_exports_exactly_500(session):
    _make_client(session)
    _make_project(session, 10, "B1_[LR223] ПрактикМ")
    group = limit_groups.create_group(
        session, client_id=2, name="ПрактикМ", daily_limit=500, project_ids=[10]
    )

    # День 1: пришло 700, выгружено 500, осталось 200 pending.
    for index in range(700):
        _make_lead(session, 1000 + index, 10, when=_at(DAY1, 8) + timedelta(minutes=index))
    _mark_exported(session, _plan(session, [10], day=DAY1).lead_ids(), when=_at(DAY1, 18))
    assert limit_groups.get_group_quota(session, int(group.id), day=DAY1).pending_total == 200

    # День 2: +600 новых.
    for index in range(600):
        _make_lead(session, 2000 + index, 10, when=_at(DAY2, 8) + timedelta(minutes=index))

    plan = _plan(session, [10], day=DAY2)
    assert plan.total == 500
    ids = plan.lead_ids()
    assert ids[:200] == list(range(1500, 1700))   # 200 старых
    assert ids[200:500] == list(range(2000, 2300))  # 300 новых

    _mark_exported(session, ids, when=_at(DAY2, 18))
    quota = limit_groups.get_group_quota(session, int(group.id), day=DAY2)
    assert quota.exported_today == 500
    assert quota.pending_total == 300  # 300 новых переходят на день 3
    assert quota.limit_reached is True

    # День 3: эти 300 уходят первыми.
    day3 = date(2026, 3, 12)
    plan3 = _plan(session, [10], day=day3)
    assert plan3.total == 300
    assert plan3.lead_ids() == list(range(2300, 2600))


# -------------------------------------------------------------------
# 4. Лимит увеличен 500 -> 700 после достижения
# -------------------------------------------------------------------

def test_limit_increased_500_to_700(session):
    _make_client(session)
    _make_project(session, 10, "B1_[LR223] ПрактикМ")
    group = limit_groups.create_group(
        session, client_id=2, name="ПрактикМ", daily_limit=500, project_ids=[10]
    )
    # Пришло 800: 500 выгружено, 300 ждут.  После поднятия лимита до 700
    # остаётся 200 мест — они и должны уйти следующим запуском.
    for index in range(800):
        _make_lead(session, 1000 + index, 10, when=_at(DAY1, 8) + timedelta(minutes=index))
    _mark_exported(session, _plan(session, [10], day=DAY1).lead_ids(), when=_at(DAY1, 18))

    quota_before = limit_groups.get_group_quota(session, int(group.id), day=DAY1)
    assert quota_before.exported_today == 500
    assert quota_before.pending_total == 300
    assert quota_before.remaining_today == 0

    # Админ поднимает лимит, ничего не удаляя и не пересоздавая.
    limit_groups.update_group(
        session, group_id=int(group.id), client_id=2, daily_limit=700
    )

    quota_after = limit_groups.get_group_quota(session, int(group.id), day=DAY1)
    assert quota_after.exported_today == 500
    assert quota_after.remaining_today == 200  # 700 - 500
    assert quota_after.limit_reached is False

    # Следующий запуск экспорта доводит FIFO-очередь до нового потолка.
    plan = _plan(session, [10], day=DAY1)
    assert plan.total == 200
    assert plan.lead_ids() == list(range(1500, 1700))

    _mark_exported(session, plan.lead_ids(), when=_at(DAY1, 19))
    quota_final = limit_groups.get_group_quota(session, int(group.id), day=DAY1)
    assert quota_final.exported_today == 700
    assert quota_final.pending_total == 100
    assert quota_final.limit_reached is True  # достигнут новый потолок


# -------------------------------------------------------------------
# 5. Лимит уменьшен 500 -> 400 при exported=300
# -------------------------------------------------------------------

def test_limit_decreased_500_to_400_with_exported_300(session):
    _make_client(session)
    _make_project(session, 10, "B1_[LR223] ПрактикМ")
    group = limit_groups.create_group(
        session, client_id=2, name="ПрактикМ", daily_limit=500, project_ids=[10]
    )
    for index in range(400):
        _make_lead(session, 1000 + index, 10, when=_at(DAY1, 8) + timedelta(minutes=index))
    _mark_exported(session, _plan(session, [10], day=DAY1).lead_ids()[:300], when=_at(DAY1, 18))

    limit_groups.update_group(session, group_id=int(group.id), client_id=2, daily_limit=400)
    quota = limit_groups.get_group_quota(session, int(group.id), day=DAY1)
    assert quota.exported_today == 300
    assert quota.remaining_today == 100

    plan = _plan(session, [10], day=DAY1)
    assert plan.total == 100


# -------------------------------------------------------------------
# 6. Лимит уменьшен 500 -> 300 при exported=450
# -------------------------------------------------------------------

def test_limit_decreased_below_exported_blocks_today_but_resets_tomorrow(session):
    _make_client(session)
    _make_project(session, 10, "B1_[LR223] ПрактикМ")
    group = limit_groups.create_group(
        session, client_id=2, name="ПрактикМ", daily_limit=500, project_ids=[10]
    )
    for index in range(450):
        _make_lead(session, 1000 + index, 10, when=_at(DAY1, 8) + timedelta(minutes=index))
    _mark_exported(session, _plan(session, [10], day=DAY1).lead_ids(), when=_at(DAY1, 18))

    limit_groups.update_group(session, group_id=int(group.id), client_id=2, daily_limit=300)
    quota = limit_groups.get_group_quota(session, int(group.id), day=DAY1)
    assert quota.exported_today == 450
    assert quota.remaining_today == 0        # ничего больше сегодня
    assert quota.limit_reached is True

    # Сегодня не выгружается ничего, но уже отправленное не откатывается.
    assert _plan(session, [10], day=DAY1).total == 0

    # Завтра квота уже новая: 300. Старые pending (200) идут первыми,
    # новых лидов на остаток 100 не хватает — они ждут следующего дня.
    for index in range(200):
        _make_lead(session, 3000 + index, 10, when=_at(DAY2, 8) + timedelta(minutes=index))
    plan2 = _plan(session, [10], day=DAY2)
    assert plan2.total == 200
    assert plan2.lead_ids() == list(range(3000, 3200))

    # Если новых лидов больше остатка, они дожидаются следующего дня.
    for index in range(150):
        _make_lead(session, 4000 + index, 10, when=_at(DAY2, 14) + timedelta(minutes=index))
    plan3 = _plan(session, [10], day=DAY2)
    assert plan3.total == 300
    assert plan3.lead_ids()[:200] == list(range(3000, 3200))  # старые первыми
    assert plan3.lead_ids()[200:] == list(range(4000, 4100))  # 100 новых следом


# 6b. 500 выгружено, 200 pending, лимит снижен до 300.
def test_limit_decreased_500_to_300_with_500_exported(session):
    _make_client(session)
    _make_project(session, 10, "B1_[LR223] ПрактикМ")
    group = limit_groups.create_group(
        session, client_id=2, name="ПрактикМ", daily_limit=500, project_ids=[10]
    )
    for index in range(700):
        _make_lead(session, 1000 + index, 10, when=_at(DAY1, 8) + timedelta(minutes=index))
    _mark_exported(session, _plan(session, [10], day=DAY1).lead_ids(), when=_at(DAY1, 18))
    assert limit_groups.get_group_quota(session, int(group.id), day=DAY1).pending_total == 200

    limit_groups.update_group(session, group_id=int(group.id), client_id=2, daily_limit=300)
    assert _plan(session, [10], day=DAY1).total == 0

    # Завтра: дневной лимит 300, сначала уходят старые 200 pending,
    # остаётся 100 мест для новых.
    for index in range(150):
        _make_lead(session, 3000 + index, 10, when=_at(DAY2, 8) + timedelta(minutes=index))
    plan2 = _plan(session, [10], day=DAY2)
    assert plan2.total == 300
    assert plan2.lead_ids()[:200] == list(range(1500, 1700))
    assert plan2.lead_ids()[200:] == list(range(3000, 3100))


# -------------------------------------------------------------------
# 7. Один project_id нельзя добавить в две группы
# -------------------------------------------------------------------

def test_project_cannot_be_in_two_groups(session):
    _make_client(session)
    _make_project(session, 10, "B1_[LR223] ПрактикМ")
    _make_project(session, 11, "B1_[LR224] ПрактикМ")
    first = limit_groups.create_group(
        session, client_id=2, name="Первая", daily_limit=100, project_ids=[10]
    )
    limit_groups.create_group(
        session, client_id=2, name="Вторая", daily_limit=100, project_ids=[11]
    )

    with pytest.raises(limit_groups.DailyExportLimitGroupError) as exc:
        limit_groups.create_group(
            session, client_id=2, name="Третья", daily_limit=100, project_ids=[10, 11]
        )
    assert "уже входит" in str(exc.value)

    # Инвариант БД: project_id уникален.
    assert limit_groups.find_group_by_project_id(session, 10).id == first.id
    assert limit_groups.find_group_by_project_id(session, 11).name == "Вторая"

    from sqlalchemy.exc import IntegrityError

    session.add(
        models.ProjectDailyExportLimitGroupProject(
            group_id=first.id, project_id=11, created_at=now_msk()
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# -------------------------------------------------------------------
# 8. Проект без группы экспортируется без нового ограничения
# -------------------------------------------------------------------

def test_project_without_group_is_not_limited(session):
    _make_client(session)
    _make_project(session, 10, "B1_[LR223] ПрактикМ")
    _make_project(session, 11, "B1_[LR999] Свободный")
    limit_groups.create_group(
        session, client_id=2, name="ПрактикМ", daily_limit=1, project_ids=[10]
    )

    for index in range(20):
        _make_lead(session, 1000 + index, 10, when=_at(DAY1, 8) + timedelta(minutes=index))
    for index in range(20):
        _make_lead(session, 3000 + index, 11, when=_at(DAY1, 8) + timedelta(minutes=index))

    plan = _plan(session, [10, 11], day=DAY1)
    grouped_ids = [item.lead_id for item in plan.items if item.group_id is not None]
    free_ids = [item.lead_id for item in plan.items if item.group_id is None]

    assert len(grouped_ids) == 1     # лимит группы = 1
    assert len(free_ids) == 20       # проект вне группы выгружается целиком


# -------------------------------------------------------------------
# 9/10. Повторный cron и ошибка Google не двигают метку
# -------------------------------------------------------------------

def test_google_failure_does_not_mark_exported(session):
    from backend.app import crud

    _make_client(session)
    _make_project(session, 10, "B1_[LR223] ПрактикМ")
    limit_groups.create_group(
        session, client_id=2, name="ПрактикМ", daily_limit=100, project_ids=[10]
    )
    for index in range(5):
        _make_lead(session, 1000 + index, 10, when=_at(DAY1, 8) + timedelta(minutes=index))

    import tool_export_provider_leads as export_mod

    # Ошибка Google -> метка не ставится.
    class FailingService:
        def spreadsheets(self):
            raise AssertionError("должно быть перехвачено до вызова Google")

    original = export_mod._get_existing_ids
    original_meta = export_mod._get_sheet_meta
    try:
        export_mod._get_existing_ids = lambda *a, **k: {"vid-1", "vid-2"}
        export_mod._get_sheet_meta = lambda *a, **k: (1, 100)

        def boom(*args, **kwargs):
            raise RuntimeError("Google API error")

        export_mod._safe_google_call = boom
        export_mod._run_export(
            log=__import__("logging").getLogger("test"),
            service=None,
            SessionLocal=lambda: session,
            sheet_id="s",
            sheet_name="n",
        )
    finally:
        export_mod._get_existing_ids = original
        export_mod._get_sheet_meta = original_meta

    rows = session.execute(select(models.ProviderLead.id)).scalars().all()
    assert rows, "лиды остаются в очереди"
    pending = session.execute(
        select(models.ProviderLead).where(models.ProviderLead.provider_sheet_exported_at.is_(None))
    ).scalars().all()
    assert len(pending) == 5, "при ошибке Google ни одна строка не помечается выгруженной"


def test_repeated_cron_does_not_duplicate(session):
    _make_client(session)
    _make_project(session, 10, "B1_[LR223] ПрактикМ")
    limit_groups.create_group(
        session, client_id=2, name="ПрактикМ", daily_limit=100, project_ids=[10]
    )
    for index in range(3):
        _make_lead(session, 1000 + index, 10, when=_at(DAY1, 8) + timedelta(minutes=index))

    first = _plan(session, [10], day=DAY1)
    _mark_exported(session, first.lead_ids())

    second = _plan(session, [10], day=DAY1)
    assert second.total == 0, "повторный cron не выгружает уже отправленные строки"


# -------------------------------------------------------------------
# 11. Удаление группы освобождает проекты
# -------------------------------------------------------------------

def test_delete_group_frees_projects_and_pending_flow_again(session):
    _make_client(session)
    _make_project(session, 10, "B1_[LR223] ПрактикМ")
    group = limit_groups.create_group(
        session, client_id=2, name="ПрактикМ", daily_limit=1, project_ids=[10]
    )
    for index in range(5):
        _make_lead(session, 1000 + index, 10, when=_at(DAY1, 8) + timedelta(minutes=index))

    assert _plan(session, [10], day=DAY1).total == 1
    assert limit_groups.find_group_by_project_id(session, 10) is not None

    limit_groups.delete_group(session, group_id=int(group.id), client_id=2)

    assert limit_groups.find_group_by_project_id(session, 10) is None
    plan = _plan(session, [10], day=DAY1)
    assert plan.total == 5, "после удаления группы pending снова выгружаются без ограничения"
    # Лиды не удалены
    assert session.execute(select(models.ProviderLead.id)).scalars().all() is not None
    assert len(session.execute(select(models.ProviderLead.id)).scalars().all()) == 5


# -------------------------------------------------------------------
# 12. Pixel остаётся полностью неизменным
# -------------------------------------------------------------------

def test_pixel_leads_never_touch_provider_export_state(session):
    _make_client(session)
    pixel_project = models.Project(
        id=20,
        user_id=2,
        name="Pixel [LR777] ПрактикМ",
        tag="t",
        collection_source="Пиксель",
        data_source_code="UNMAPPED",
        status="Активен",
        delivery_status="Активна",
        data_limit=0,
        numbers_today=0,
        numbers_total=0,
        days_received="",
        sources_count=0,
        created_at=now_msk(),
        updated_at=now_msk(),
    )
    session.add(pixel_project)
    session.commit()

    # Пиксельные проекты нельзя включить в группу.
    with pytest.raises(limit_groups.DailyExportLimitGroupError):
        limit_groups.create_group(
            session, client_id=2, name="Пиксель", daily_limit=10, project_ids=[20]
        )

    # Пиксельные лиды не попадают в provider-план и не получают provider-метку.
    _make_lead(session, 5000, 20, when=_at(DAY1), lead_source="pixel")
    _make_lead(session, 5001, 20, when=_at(DAY1), lead_source="pixel")

    plan = _plan(session, [20], day=DAY1)
    assert plan.total == 0, "Pixel-лиды не участвуют в provider-экспорте"

    pixel_rows = session.execute(
        select(models.ProviderLead).where(models.ProviderLead.lead_source == "pixel")
    ).scalars().all()
    assert all(row.provider_sheet_exported_at is None for row in pixel_rows)
    assert all(row.client_sheet_exported_at is None for row in pixel_rows)


# -------------------------------------------------------------------
# Дополнительно: лимит пачки и дедупликация Telegram
# -------------------------------------------------------------------

def test_batch_limit_cuts_exactly_to_remaining_seats(session):
    _make_client(session)
    _make_project(session, 10, "B1_[LR223] ПрактикМ")
    limit_groups.create_group(
        session, client_id=2, name="ПрактикМ", daily_limit=7, project_ids=[10]
    )
    for index in range(20):
        _make_lead(session, 1000 + index, 10, when=_at(DAY1, 8) + timedelta(minutes=index))

    plan = _plan(session, [10], day=DAY1)
    assert plan.total == 7, "осталось 7 мест -> выгружаются строго первые 7"
    assert plan.lead_ids() == list(range(1000, 1007))


def test_telegram_notification_is_deduplicated(session, monkeypatch):
    _make_client(session)
    _make_project(session, 10, "B1_[LR223] ПрактикМ")
    group = limit_groups.create_group(
        session, client_id=2, name="ПрактикМ", daily_limit=5, project_ids=[10]
    )
    for index in range(5):
        _make_lead(session, 1000 + index, 10, when=_at(DAY1, 8) + timedelta(minutes=index))
    _mark_exported(session, _plan(session, [10], day=DAY1).lead_ids(), when=_at(DAY1, 18))

    sent = []

    def fake_send(*, db_sess, chat_id, text, parse_mode=None, metadata=None, bot_token="", kind="system"):
        sent.append(text)
        import backend.app.notifications as notifications_mod

        return notifications_mod.NotificationResult(
            delivered=True, channel="telegram_outbox", reason="queued", notification_id=len(sent)
        )

    monkeypatch.setattr(limit_groups.notifications, "send_system_notification", fake_send)

    quota = limit_groups.get_group_quota(session, int(group.id), day=DAY1)
    assert limit_groups.notify_limit_reached(
        session, group=group, quota=quota, chat_id="-100", day=DAY1
    ) is True
    # Повторный cron-run того же дня при том же лимите молчит.
    assert limit_groups.notify_limit_reached(
        session, group=group, quota=quota, chat_id="-100", day=DAY1
    ) is False
    assert len(sent) == 1
    assert "Лимит группы проектов на день достигнут" in sent[0]
    assert "автоматически не отключались" in sent[0]

    # Увеличение лимита и достижение нового потолка -> новое уведомление.
    limit_groups.update_group(session, group_id=int(group.id), client_id=2, daily_limit=10)
    for index in range(5, 10):
        _make_lead(session, 2000 + index, 10, when=_at(DAY1, 9) + timedelta(minutes=index))
    _mark_exported(session, _plan(session, [10], day=DAY1).lead_ids(), when=_at(DAY1, 19))
    quota2 = limit_groups.get_group_quota(session, int(group.id), day=DAY1)
    assert limit_groups.notify_limit_reached(
        session, group=group, quota=quota2, chat_id="-100", day=DAY1
    ) is True
    assert len(sent) == 2


# -------------------------------------------------------------------
# Вспомогательное
# -------------------------------------------------------------------

def _mark_exported(s, lead_ids, when: datetime | None = None):
    """
    Локальный аналог ``_mark_provider_exported`` без Google-зависимости.

    ``when`` нужен, чтобы метка попала в нужный календарный день сценария:
    «выгружено сегодня» считается по бизнес-таймзоне, а не по времени запуска теста.
    """
    ids = sorted({int(value) for value in lead_ids})
    if not ids:
        return 0
    stamp = when if when is not None else as_local_naive(now_msk())
    for row in s.execute(
        select(models.ProviderLead).where(
            models.ProviderLead.id.in_(ids),
            models.ProviderLead.provider_sheet_exported_at.is_(None),
        )
    ).scalars().all():
        row.provider_sheet_exported_at = stamp
    s.commit()
    return len(ids)
