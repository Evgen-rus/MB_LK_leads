"""Общие контракты долговечных массовых project-операций.

Модуль не вызывает внешние сервисы. Он содержит только статусы, нейтральные
сообщения для UI и небольшой контракт, который worker использует для передачи
снимка проекта обработчику provider-интеграции.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Literal, Optional, Protocol


PROJECT_OPERATION_STATUS_QUEUED = "queued"
PROJECT_OPERATION_STATUS_RUNNING = "running"
PROJECT_OPERATION_STATUS_WAITING_RETRY = "waiting_retry"
PROJECT_OPERATION_STATUS_COMPLETED = "completed"
PROJECT_OPERATION_STATUS_NEEDS_ATTENTION = "needs_attention"

PROJECT_OPERATION_ACTIVE_STATUSES = frozenset(
    {
        PROJECT_OPERATION_STATUS_QUEUED,
        PROJECT_OPERATION_STATUS_RUNNING,
        PROJECT_OPERATION_STATUS_WAITING_RETRY,
    }
)
PROJECT_OPERATION_TERMINAL_STATUSES = frozenset(
    {
        PROJECT_OPERATION_STATUS_COMPLETED,
        PROJECT_OPERATION_STATUS_NEEDS_ATTENTION,
    }
)

PROJECT_OPERATION_ITEM_STATUS_QUEUED = PROJECT_OPERATION_STATUS_QUEUED
PROJECT_OPERATION_ITEM_STATUS_RUNNING = PROJECT_OPERATION_STATUS_RUNNING
PROJECT_OPERATION_ITEM_STATUS_WAITING_RETRY = PROJECT_OPERATION_STATUS_WAITING_RETRY
PROJECT_OPERATION_ITEM_STATUS_COMPLETED = PROJECT_OPERATION_STATUS_COMPLETED
PROJECT_OPERATION_ITEM_STATUS_NEEDS_ATTENTION = PROJECT_OPERATION_STATUS_NEEDS_ATTENTION

PROJECT_OPERATION_NEUTRAL_WAITING_MESSAGE = "Сервис обработки данных временно недоступен. Операция продолжится автоматически."
PROJECT_OPERATION_NEUTRAL_QUEUED_MESSAGE = "Операция сохранена и ожидает выполнения."
PROJECT_OPERATION_NEUTRAL_RUNNING_MESSAGE = "Операция выполняется."
PROJECT_OPERATION_NEUTRAL_COMPLETED_MESSAGE = "Операция выполнена."
PROJECT_OPERATION_NEUTRAL_ATTENTION_MESSAGE = "Не удалось применить изменения к одному или нескольким проектам. Обратитесь к менеджеру."

ProjectOperationItemStatus = Literal[
    PROJECT_OPERATION_STATUS_QUEUED,
    PROJECT_OPERATION_STATUS_RUNNING,
    PROJECT_OPERATION_STATUS_WAITING_RETRY,
    PROJECT_OPERATION_STATUS_COMPLETED,
    PROJECT_OPERATION_STATUS_NEEDS_ATTENTION,
]


class ActiveProjectOperationError(RuntimeError):
    """Попытка запустить вторую provider-операцию для клиента."""

    def __init__(self, client_id: int, operation_id: Optional[int] = None):
        self.client_id = int(client_id)
        self.operation_id = int(operation_id) if operation_id is not None else None
        suffix = f" (operation_id={self.operation_id})" if self.operation_id is not None else ""
        super().__init__(f"active project operation already exists for client {self.client_id}{suffix}")


@dataclass(frozen=True)
class ProjectOperationItemContext:
    """Снимок item, передаваемый внешнему обработчику без открытой DB-сессии."""

    item_id: int
    operation_id: int
    client_id: int
    project_id: int
    provider_project_id: Optional[str]
    project_name: Optional[str]
    state_snapshot: Optional[Dict[str, Any]] = None
    payload_snapshot: Optional[Dict[str, Any]] = None
    attempt_count: int = 0


@dataclass(frozen=True)
class ProjectOperationItemResult:
    """Результат одного шага обработчика.

    ``waiting_retry`` означает временную ошибку: worker сохранит item и
    продолжит позже. ``needs_attention`` — постоянная ошибка проекта.
    """

    status: ProjectOperationItemStatus
    error: Optional[str] = None
    next_attempt_at: Optional[datetime] = None
    result_snapshot: Optional[Dict[str, Any]] = None
    message: Optional[str] = None

    @classmethod
    def completed(cls, result_snapshot: Optional[Dict[str, Any]] = None) -> "ProjectOperationItemResult":
        return cls(
            status=PROJECT_OPERATION_STATUS_COMPLETED,
            result_snapshot=result_snapshot,
        )

    @classmethod
    def waiting_retry(
        cls,
        *,
        next_attempt_at: Optional[datetime] = None,
        error: Optional[str] = None,
    ) -> "ProjectOperationItemResult":
        return cls(
            status=PROJECT_OPERATION_STATUS_WAITING_RETRY,
            next_attempt_at=next_attempt_at,
            error=error,
        )

    @classmethod
    def needs_attention(cls, *, error: Optional[str] = None) -> "ProjectOperationItemResult":
        return cls(
            status=PROJECT_OPERATION_STATUS_NEEDS_ATTENTION,
            error=error,
        )


class ProjectOperationProcessor(Protocol):
    """Минимальный интерфейс provider-обработчика для worker.

    Реализация вызывается вне DB-сессии. Это позволяет безопасно делать
    сетевой вызов, а затем сохранять результат отдельной короткой транзакцией.
    """

    def process_item(self, item: ProjectOperationItemContext) -> ProjectOperationItemResult:
        ...


def operation_message(status: str) -> str:
    """Возвращает безопасное для клиента сообщение без названия поставщика."""
    if status == PROJECT_OPERATION_STATUS_WAITING_RETRY:
        return PROJECT_OPERATION_NEUTRAL_WAITING_MESSAGE
    if status == PROJECT_OPERATION_STATUS_QUEUED:
        return PROJECT_OPERATION_NEUTRAL_QUEUED_MESSAGE
    if status == PROJECT_OPERATION_STATUS_RUNNING:
        return PROJECT_OPERATION_NEUTRAL_RUNNING_MESSAGE
    if status == PROJECT_OPERATION_STATUS_NEEDS_ATTENTION:
        return PROJECT_OPERATION_NEUTRAL_ATTENTION_MESSAGE
    return PROJECT_OPERATION_NEUTRAL_COMPLETED_MESSAGE


def is_active_status(status: Optional[str]) -> bool:
    return str(status or "") in PROJECT_OPERATION_ACTIVE_STATUSES


def is_terminal_status(status: Optional[str]) -> bool:
    return str(status or "") in PROJECT_OPERATION_TERMINAL_STATUSES

