"""
Файл: backend/app/daily_export_limit_groups.py
Назначение: бизнес-логика «Лимита группы проектов на день».

Модуль намеренно не знает про HTTP и не знает про Google Sheets.  Здесь только
работа с БД: чтение групп, дневной расход, FIFO-очередь и принятие решения
«что именно сегодня можно выгружать».

Ключевые инварианты:
- источник истины о членстве — только ``project_id`` (таблица
  ``project_daily_export_limit_group_projects``), а не ``[LRxxx]`` и не regex имени;
- один ``project_id`` не может быть в двух группах (UNIQUE в БД + проверка);
- «выгружено сегодня» считается по ``provider_leads.provider_sheet_exported_at``,
  а не по приходу лидов, ``Project.data_limit``, остатку тарифа или ``numbers_today``;
- календарные границы дня считаются в ``SHEETS_TZ`` (МСК), как и остальные
  дневные показатели проекта;
- лиды сверх лимита не удаляются и не пропускаются: они остаются в FIFO-очереди
  и уходят на следующие дни.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import Select, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import crud, models, notifications
from .time_utils import as_local_naive, now_msk


# Порог, при котором группа считается «лимит достигнут».
# Сравнение строгое (>=), поэтому при лимите 500 и 500 выгруженных
# группа уже считается исчерпанной.
LIMIT_REACHED_COMPARATOR = "ge"

TELEGRAM_KIND = "project_daily_export_limit_reached"

# Безопасный запас от границы: за день бизнес-таймзоны не «переезжают», но
# подстраховка от полуночи стоит нам одного сравнения.
_DAY_TZ_FALLBACK_HOURS = 3


class DailyExportLimitGroupError(Exception):
    """Ошибка бизнес-правил группы лимита.  ``status_code`` — ответ API."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class DailyExportLimitQuota:
    """Дневная квота одной группы."""

    group_id: int
    daily_limit: int
    exported_today: int
    pending_total: int

    @property
    def remaining_today(self) -> int:
        """Сколько строк ещё разрешено выгрузить сегодня.

        Уменьшение лимита администратором ниже уже выгруженного даёт 0:
        уже отправленные строки назад не удаляются, но и новых не добавить.
        """
        return max(0, int(self.daily_limit) - int(self.exported_today))

    @property
    def limit_reached(self) -> bool:
        return int(self.exported_today) >= int(self.daily_limit)

    @property
    def exhausted(self) -> bool:
        """Нет свободных мест сегодня (включая случай уменьшения лимита)."""
        return self.remaining_today <= 0


@dataclass
class ExportPlanItem:
    """Одна строка, разрешённая к выгрузке сегодня."""

    lead_id: int
    group_id: Optional[int]
    quota: Optional[DailyExportLimitQuota] = field(default=None)


@dataclass
class ExportPlan:
    """
    Решение «что выгружать» до похода в Google API.

    Строки с ``group_id is None`` — проекты вне групп: они не ограничены.
    Строки с ``group_id`` уже урезаны по дневной квоте своей группы.
    """

    items: List[ExportPlanItem]
    # Квоты всех затронутых групп, включая те, у которых сегодня 0 мест.
    # Нужны вызывающему коду для логов и Telegram-уведомления.
    quotas: Dict[int, DailyExportLimitQuota] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def grouped(self) -> int:
        return sum(1 for item in self.items if item.group_id is not None)

    @property
    def ungrouped(self) -> int:
        return self.total - self.grouped

    def lead_ids(self) -> List[int]:
        return [item.lead_id for item in self.items]

    def group_ids(self) -> List[int]:
        seen: List[int] = []
        for item in self.items:
            if item.group_id is not None and item.group_id not in seen:
                seen.append(item.group_id)
        return seen

    def quotas_by_group(self) -> Dict[int, DailyExportLimitQuota]:
        return dict(self.quotas)


# -------------------------------------------------------------------
# Границы календарного дня в бизнес-таймзоне
# -------------------------------------------------------------------

def business_today(now: Optional[datetime] = None) -> date:
    """Текущий календарный день в ``SHEETS_TZ`` (МСК)."""
    value = now or now_msk()
    return value.date()


def business_day_bounds(day: Optional[date] = None) -> Tuple[datetime, datetime]:
    """
    Возвращает (start, end_exclusive) выбранного дня в бизнес-таймзоне
    как naive datetime — ровно так же, как их хранит ``imported_at`` и
    ``provider_sheet_exported_at``.
    """
    target = day or business_today()
    start = as_local_naive(datetime(target.year, target.month, target.day, 0, 0, 0, tzinfo=now_msk().tzinfo))
    end = start.replace(hour=0, minute=0, second=0) + _timedelta_days(1)
    return start, end


def _timedelta_days(days: int):
    from datetime import timedelta

    return timedelta(days=days)


# -------------------------------------------------------------------
# Чтение групп и membership
# -------------------------------------------------------------------

def _group_members_stmt(client_id: Optional[int] = None) -> Select:
    stmt = select(
        models.ProjectDailyExportLimitGroupProject.group_id,
        models.ProjectDailyExportLimitGroupProject.project_id,
    )
    if client_id is not None:
        stmt = stmt.join(
            models.ProjectDailyExportLimitGroup,
            models.ProjectDailyExportLimitGroup.id == models.ProjectDailyExportLimitGroupProject.group_id,
        ).where(models.ProjectDailyExportLimitGroup.client_id == int(client_id))
    return stmt


def list_groups(db: Session, client_id: int) -> List[models.ProjectDailyExportLimitGroup]:
    """Все группы клиента.  Источник истины членства — таблица membership."""
    return list(
        db.execute(
            select(models.ProjectDailyExportLimitGroup)
            .where(models.ProjectDailyExportLimitGroup.client_id == int(client_id))
            .order_by(models.ProjectDailyExportLimitGroup.id.asc())
        ).scalars().all()
    )


def list_group_project_ids(db: Session, group_ids: Sequence[int]) -> Dict[int, List[int]]:
    """project_id по каждой группе.  Пустая выборка — пустой словарь."""
    ids = [int(value) for value in group_ids if value is not None]
    if not ids:
        return {}
    out: Dict[int, List[int]] = {group_id: [] for group_id in ids}
    rows = db.execute(
        _group_members_stmt().where(models.ProjectDailyExportLimitGroupProject.group_id.in_(ids))
    ).all()
    for group_id, project_id in rows:
        out.setdefault(int(group_id), []).append(int(project_id))
    for group_id in out:
        out[group_id] = sorted(set(out[group_id]))
    return out


def project_to_group_map(db: Session) -> Dict[int, int]:
    """Глобальная карта ``project_id -> group_id`` для рантайма экспорта.

    Благодаря UNIQUE(project_id) в БД значение всегда единственное.
    """
    rows = db.execute(
        select(
            models.ProjectDailyExportLimitGroupProject.project_id,
            models.ProjectDailyExportLimitGroupProject.group_id,
        )
    ).all()
    return {int(project_id): int(group_id) for project_id, group_id in rows}


def find_group_by_project_id(db: Session, project_id: int) -> Optional[models.ProjectDailyExportLimitGroup]:
    return db.execute(
        select(models.ProjectDailyExportLimitGroup)
        .join(
            models.ProjectDailyExportLimitGroupProject,
            models.ProjectDailyExportLimitGroupProject.group_id == models.ProjectDailyExportLimitGroup.id,
        )
        .where(models.ProjectDailyExportLimitGroupProject.project_id == int(project_id))
    ).scalars().first()


def get_group(db: Session, group_id: int) -> Optional[models.ProjectDailyExportLimitGroup]:
    return db.get(models.ProjectDailyExportLimitGroup, int(group_id))


def list_group_names_by_project_ids(db: Session, project_ids: Sequence[int]) -> Dict[int, str]:
    """Название группы для каждого project_id (для подсказок в UI)."""
    ids = [int(value) for value in project_ids if value is not None]
    if not ids:
        return {}
    rows = db.execute(
        select(
            models.ProjectDailyExportLimitGroupProject.project_id,
            models.ProjectDailyExportLimitGroup.name,
        )
        .join(
            models.ProjectDailyExportLimitGroup,
            models.ProjectDailyExportLimitGroup.id == models.ProjectDailyExportLimitGroupProject.group_id,
        )
        .where(models.ProjectDailyExportLimitGroupProject.project_id.in_(ids))
    ).all()
    return {int(project_id): str(name) for project_id, name in rows}


# -------------------------------------------------------------------
# Валидация правок группы
# -------------------------------------------------------------------

def _validate_daily_limit(value: int) -> int:
    limit = int(value)
    if limit <= 0:
        raise DailyExportLimitGroupError("Дневной лимит должен быть больше нуля.")
    if limit > 10_000_000:
        raise DailyExportLimitGroupError("Слишком большой дневной лимит.")
    return limit


def validate_group_projects(
    db: Session,
    *,
    client_id: int,
    project_ids: Sequence[int],
    group_id: Optional[int] = None,
) -> List[int]:
    """
    Проверяет, что проекты существуют, принадлежат клиенту и свободны.

    Возвращает нормализованный список project_id.  Проект, уже состоящий в
    ДРУГОЙ группе, — понятная ошибка, а не молчаливое переприсвоение.
    """
    normalized: List[int] = []
    seen = set()
    for raw in project_ids or []:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise DailyExportLimitGroupError("Некорректный идентификатор проекта.")
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)

    if not normalized:
        raise DailyExportLimitGroupError("Выберите хотя бы один проект группы.")

    projects = db.execute(
        select(models.Project).where(models.Project.id.in_(normalized))
    ).scalars().all()
    found = {int(project.id): project for project in projects}
    missing = [value for value in normalized if value not in found]
    if missing:
        raise DailyExportLimitGroupError("Некоторые проекты не найдены.", status_code=404)

    foreign = [value for value in normalized if int(found[value].user_id or 0) != int(client_id)]
    if foreign:
        raise DailyExportLimitGroupError("Проекты принадлежат другому клиенту.")

    # Пиксельные проекты в этот контур не входят: лимит описан для provider-лидов.
    pixel_projects = [value for value in normalized if crud.is_pixel_collection_source(found[value].collection_source)]
    if pixel_projects:
        raise DailyExportLimitGroupError("Пиксельные проекты нельзя включать в лимит группы.")

    owners = list_group_names_by_project_ids(db, normalized)
    own_members = (
        set(list_group_project_ids(db, [int(group_id)]).get(int(group_id), []))
        if group_id is not None
        else set()
    )
    for value in normalized:
        if value in own_members:
            # Проект уже состоит в редактируемой группе — это нормально.
            continue
        owner_name = owners.get(value)
        if owner_name is None:
            continue
        raise DailyExportLimitGroupError(
            f"Проект уже входит в группу «{owner_name}». Один проект может быть только в одной группе."
        )

    return normalized


# -------------------------------------------------------------------
# CRUD группы
# -------------------------------------------------------------------

def create_group(
    db: Session,
    *,
    client_id: int,
    name: str,
    daily_limit: int,
    project_ids: Sequence[int],
) -> models.ProjectDailyExportLimitGroup:
    name_value = str(name or "").strip()
    if not name_value:
        raise DailyExportLimitGroupError("Укажите название группы.")
    limit = _validate_daily_limit(daily_limit)
    members = validate_group_projects(db, client_id=client_id, project_ids=project_ids)

    group = models.ProjectDailyExportLimitGroup(
        client_id=int(client_id),
        name=name_value[:200],
        daily_limit=limit,
        created_at=now_msk(),
        updated_at=now_msk(),
    )
    db.add(group)
    db.flush()
    _replace_members(db, group_id=int(group.id), project_ids=members)
    try:
        db.commit()
    except IntegrityError as exc:
        # Последняя линия защиты: UNIQUE(project_id) в БД.
        db.rollback()
        raise DailyExportLimitGroupError(
            "Не удалось сохранить группу: часть проектов уже входит в другую группу.",
            status_code=409,
        ) from exc
    db.refresh(group)
    return group


def update_group(
    db: Session,
    *,
    group_id: int,
    client_id: int,
    name: Optional[str] = None,
    daily_limit: Optional[int] = None,
    project_ids: Optional[Sequence[int]] = None,
) -> models.ProjectDailyExportLimitGroup:
    group = get_group(db, group_id)
    if group is None:
        raise DailyExportLimitGroupError("Группа не найдена.", status_code=404)
    if int(group.client_id) != int(client_id):
        raise DailyExportLimitGroupError("Нет доступа к этой группе.", status_code=403)

    if name is not None:
        name_value = str(name).strip()
        if not name_value:
            raise DailyExportLimitGroupError("Укажите название группы.")
        group.name = name_value[:200]

    if daily_limit is not None:
        # Новое значение применяется сразу; уже выгруженное за день назад не удаляется.
        # Если новый лимит ниже выгруженного, «остаток дня» просто становится 0
        # (см. DailyExportLimitQuota.remaining_today), а завтра лимит уже новый.
        group.daily_limit = _validate_daily_limit(daily_limit)

    if project_ids is not None:
        members = validate_group_projects(
            db,
            client_id=client_id,
            project_ids=project_ids,
            group_id=int(group.id),
        )
        _replace_members(db, group_id=int(group.id), project_ids=members)

    group.updated_at = now_msk()
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DailyExportLimitGroupError(
            "Не удалось сохранить группу: часть проектов уже входит в другую группу.",
            status_code=409,
        ) from exc
    db.refresh(group)
    return group


def delete_group(db: Session, *, group_id: int, client_id: int) -> bool:
    """
    Удаляет группу и её membership.

    Проекты НЕ удаляются и их статусы не меняются: они просто становятся
    обычными негруппированными проектами, и их pending-лиды снова
    выгружаются без группового ограничения.
    """
    group = get_group(db, group_id)
    if group is None:
        raise DailyExportLimitGroupError("Группа не найдена.", status_code=404)
    if int(group.client_id) != int(client_id):
        raise DailyExportLimitGroupError("Нет доступа к этой группе.", status_code=403)
    db.execute(
        delete(models.ProjectDailyExportLimitGroupProject).where(
            models.ProjectDailyExportLimitGroupProject.group_id == int(group.id)
        )
    )
    db.delete(group)
    db.commit()
    return True


def _replace_members(db: Session, *, group_id: int, project_ids: Iterable[int]) -> None:
    db.execute(
        delete(models.ProjectDailyExportLimitGroupProject).where(
            models.ProjectDailyExportLimitGroupProject.group_id == int(group_id)
        )
    )
    now = now_msk()
    for project_id in project_ids:
        db.add(
            models.ProjectDailyExportLimitGroupProject(
                group_id=int(group_id),
                project_id=int(project_id),
                created_at=now,
            )
        )
    db.flush()


# -------------------------------------------------------------------
# Дневная статистика
# -------------------------------------------------------------------

def get_group_quota(
    db: Session,
    group_id: int,
    *,
    day: Optional[date] = None,
    project_ids: Optional[Sequence[int]] = None,
) -> DailyExportLimitQuota:
    """
    Реально выгруженное сегодня по группе + размер очереди.

    «Выгружено» = provider-лиды этой группы с
    ``provider_sheet_exported_at`` внутри текущего календарного дня.
    Это не количество пришедших лидов и не остаток тарифа.
    """
    group = get_group(db, group_id)
    if group is None:
        raise DailyExportLimitGroupError("Группа не найдена.", status_code=404)

    members = (
        [int(value) for value in project_ids]
        if project_ids is not None
        else list_group_project_ids(db, [int(group_id)]).get(int(group_id), [])
    )
    if not members:
        return DailyExportLimitQuota(
            group_id=int(group_id),
            daily_limit=int(group.daily_limit),
            exported_today=0,
            pending_total=0,
        )

    start, end = business_day_bounds(day)

    exported_stmt = select(func.count()).select_from(models.ProviderLead).where(
        models.ProviderLead.lead_source == crud.LEAD_SOURCE_PROVIDER,
        models.ProviderLead.project_id.in_(members),
        models.ProviderLead.provider_sheet_exported_at.is_not(None),
        models.ProviderLead.provider_sheet_exported_at >= start,
        models.ProviderLead.provider_sheet_exported_at < end,
    )
    exported_today = int(db.execute(exported_stmt).scalar_one() or 0)

    pending_stmt = select(func.count()).select_from(models.ProviderLead).where(
        models.ProviderLead.lead_source == crud.LEAD_SOURCE_PROVIDER,
        models.ProviderLead.project_id.in_(members),
        models.ProviderLead.provider_sheet_exported_at.is_(None),
    )
    pending_total = int(db.execute(pending_stmt).scalar_one() or 0)

    return DailyExportLimitQuota(
        group_id=int(group_id),
        daily_limit=int(group.daily_limit),
        exported_today=exported_today,
        pending_total=pending_total,
    )


# -------------------------------------------------------------------
# План выгрузки: FIFO + дневные квоты
# -------------------------------------------------------------------

def build_export_plan(
    db: Session,
    *,
    project_ids: Sequence[int],
    day: Optional[date] = None,
    ungrouped_batch_limit: Optional[int] = None,
) -> ExportPlan:
    """
    Решает, какие строки разрешено выгрузить сегодня.

    Порядок — строго FIFO по ``imported_at ASC, id ASC``, чтобы старые
    невыгруженные лиды всегда уходили раньше новых.

    Логика по группам:
    - строка в очереди сверх квоты остаётся в БД (NULL-метка) и уйдёт завтра;
    - если админ уменьшил лимит ниже уже выгруженного, группа сегодня ничего
      не отправляет (remaining_today == 0), но завтра квота уже новая.
    """
    members = [int(value) for value in project_ids if value is not None]
    if not members:
        return ExportPlan(items=[])

    group_by_project = project_to_group_map(db)
    ungrouped_set = {pid for pid in members if pid not in group_by_project}

    # Одна общая FIFO-выборка по всем проектам: imported_at ASC, id ASC.
    # Именно этот порядок гарантирует, что старые pending-лиды уходят раньше новых.
    rows = db.execute(
        select(models.ProviderLead.id, models.ProviderLead.project_id)
        .where(
            models.ProviderLead.lead_source == crud.LEAD_SOURCE_PROVIDER,
            models.ProviderLead.project_id.in_(members),
            models.ProviderLead.prov_created_at.isnot(None),
            models.ProviderLead.provider_sheet_exported_at.is_(None),
        )
        .order_by(models.ProviderLead.imported_at.asc(), models.ProviderLead.id.asc())
    ).all()

    grouped_project_ids = [pid for pid in members if pid in group_by_project]
    quotas: Dict[int, DailyExportLimitQuota] = {}
    for group_id in sorted({group_by_project[pid] for pid in grouped_project_ids}):
        quotas[group_id] = get_group_quota(db, group_id, day=day)

    per_group_remaining = {group_id: quota.remaining_today for group_id, quota in quotas.items()}

    items: List[ExportPlanItem] = []
    ungrouped_kept = 0
    for lead_id, project_id in rows:
        project_id = int(project_id)
        group_id = group_by_project.get(project_id)
        if group_id is None:
            # Проект вне групп: выгружается как раньше, без нового ограничения.
            if ungrouped_batch_limit is not None and ungrouped_kept >= ungrouped_batch_limit:
                continue
            ungrouped_kept += 1
            items.append(ExportPlanItem(lead_id=int(lead_id), group_id=None))
            continue
        # Проект в группе: строгий FIFO + жёсткое урезание по дневной квоте.
        # Ни одна строка сверх остатка в план не попадает: она остаётся pending.
        if per_group_remaining.get(group_id, 0) <= 0:
            continue
        per_group_remaining[group_id] -= 1
        items.append(
            ExportPlanItem(lead_id=int(lead_id), group_id=group_id, quota=quotas[group_id])
        )

    return ExportPlan(items=items, quotas=quotas)


# -------------------------------------------------------------------
# Telegram-уведомление о достижении дневного лимита
# -------------------------------------------------------------------

def _build_limit_reached_message(
    *,
    client_name: str,
    client_login: str,
    client_id: int,
    group_name: str,
    quota: DailyExportLimitQuota,
) -> str:
    return (
        "<b>Лимит группы проектов на день достигнут</b>\n\n"
        f'Клиент: <code>{html.escape(str(client_name or client_login or ""))}</code> (id={int(client_id)})\n'
        f"Группа: <code>{html.escape(str(group_name))}</code>\n"
        f"Лимит: <b>{int(quota.daily_limit)}</b>\n"
        f"Выгружено сегодня: <b>{int(quota.exported_today)}</b>\n"
        f"Ожидают выгрузки: <b>{int(quota.pending_total)}</b>\n\n"
        "Проекты автоматически не отключались. "
        "При необходимости остановите нужные проекты вручную."
    )


def _notification_day_key(day: Optional[date] = None) -> str:
    return (day or business_today()).isoformat()


def notify_limit_reached(
    db: Session,
    *,
    group: models.ProjectDailyExportLimitGroup,
    quota: DailyExportLimitQuota,
    chat_id: str,
    day: Optional[date] = None,
) -> bool:
    """
    Ставит системное уведомление в существующий outbox.

    Дедупликация: не отправляем одно и то же сообщение каждый cron-run.
    Ключ — пара (календарный день, значение лимита).  Поэтому:
    - повторные запуски в тот же день при том же лимите молчат;
    - если лимит увеличили и достигнут новый потолок — придёт новое сообщение;
    - если лимит уменьшили ниже уже выгруженного, ключ сменится, но
      условие ``quota.limit_reached`` по-прежнему проверяется, поэтому
      уведомление придёт один раз и без «залипания» на каждом запуске.
    """
    if not quota.limit_reached:
        return False
    if not str(chat_id or "").strip():
        return False

    day_key = _notification_day_key(day)
    notified_day = str(getattr(group, "limit_reached_notified_on", "") or "")
    notified_limit = getattr(group, "limit_reached_notified_limit", None)
    if notified_day == day_key and notified_limit is not None and int(notified_limit) == int(quota.daily_limit):
        return False

    client = db.get(models.User, int(group.client_id))
    client_name = ""
    client_login = ""
    if client is not None:
        profile = db.execute(
            select(models.ClientProfile).where(models.ClientProfile.user_id == int(group.client_id))
        ).scalars().first()
        client_name = str(getattr(profile, "name", "") or "").strip() or str(getattr(client, "display_name", "") or "").strip()
        client_login = str(getattr(client, "login", "") or "").strip()

    text = _build_limit_reached_message(
        client_name=client_name,
        client_login=client_login,
        client_id=int(group.client_id),
        group_name=str(group.name),
        quota=quota,
    )
    result = notifications.send_system_notification(
        db_sess=db,
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        metadata={
            "client_id": int(group.client_id),
            "group_id": int(group.id),
            "daily_limit": int(quota.daily_limit),
            "exported_today": int(quota.exported_today),
            "pending_total": int(quota.pending_total),
        },
    )
    if not result.delivered:
        if result.reason != "telegram_disabled":
            logging.getLogger("app").warning(
                "Failed to queue daily export limit notification for group_id=%s reason=%s",
                group.id,
                result.reason,
            )
        return False

    # Помечаем состояние только после успешной постановки в outbox.
    group.limit_reached_notified_on = day_key
    group.limit_reached_notified_limit = int(quota.daily_limit)
    group.updated_at = now_msk()
    db.commit()
    return True


def get_group_stats_map(
    db: Session,
    *,
    client_id: int,
    day: Optional[date] = None,
) -> Dict[int, DailyExportLimitQuota]:
    """Квота по всем группам клиента — для списка групп в UI."""
    groups = list_groups(db, client_id)
    if not groups:
        return {}
    members = list_group_project_ids(db, [int(group.id) for group in groups])
    out: Dict[int, DailyExportLimitQuota] = {}
    for group in groups:
        out[int(group.id)] = get_group_quota(
            db,
            int(group.id),
            day=day,
            project_ids=members.get(int(group.id), []),
        )
    return out
