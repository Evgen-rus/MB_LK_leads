"""Фундамент process-local worker для долговечных project-операций.

Worker сознательно не знает о Prostats и не содержит сетевого кода. Конкретный
provider-адаптер передаётся через ``ProjectOperationProcessor`` и вызывается с
отсоединённым snapshot, после чего результат записывается новой короткой
транзакцией.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Callable, Optional, Union

from . import crud, project_operations


Processor = Union[
    project_operations.ProjectOperationProcessor,
    Callable[[project_operations.ProjectOperationItemContext], project_operations.ProjectOperationItemResult],
]


class ProjectOperationsWorker:
    """Claim-ит одну job и обрабатывает её items строго последовательно."""

    def __init__(
        self,
        SessionLocal,
        processor: Processor,
        *,
        worker_id: Optional[str] = None,
        lease_seconds: int = crud.PROJECT_OPERATION_DEFAULT_LEASE_SECONDS,
        poll_seconds: int = 15,
    ):
        if processor is None:
            raise ValueError("processor is required")
        self.SessionLocal = SessionLocal
        self.processor = processor
        self.worker_id = str(worker_id or "").strip() or f"project-operations-{uuid.uuid4().hex[:12]}"
        self.lease_seconds = crud._bounded_project_operation_lease_seconds(lease_seconds)
        self.poll_seconds = max(1, int(poll_seconds or 15))

    def _process_item(
        self,
        item: project_operations.ProjectOperationItemContext,
    ) -> project_operations.ProjectOperationItemResult:
        processor_method = getattr(self.processor, "process_item", None)
        result = processor_method(item) if callable(processor_method) else self.processor(item)  # type: ignore[operator]
        if not isinstance(result, project_operations.ProjectOperationItemResult):
            raise TypeError("project operation processor must return ProjectOperationItemResult")
        return result

    def run_once(self) -> bool:
        """Обработать доступную job; вернуть True, если работа была найдена."""
        with self.SessionLocal() as db:
            operation = crud.claim_project_operation(
                db,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
        if operation is None:
            return False

        while True:
            # Внешний вызов находится между DB-транзакциями, а lease job
            # продлевается перед каждым item, чтобы длинная job не зависла.
            with self.SessionLocal() as db:
                if not crud.renew_project_operation_lease(
                    db,
                    operation_id=operation.id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                ):
                    return True
                item = crud.claim_next_project_operation_item(
                    db,
                    operation_id=operation.id,
                    worker_id=self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
            if item is None:
                return True

            try:
                result = self._process_item(item)
            except Exception as exc:  # noqa: BLE001 - worker не должен умереть от одного item
                # Неизвестная ошибка обработчика считается временной. Это
                # оставляет операцию долговечной; постоянные ошибки адаптер
                # должен возвращать явно как needs_attention.
                result = project_operations.ProjectOperationItemResult.waiting_retry(
                    error=str(exc)[:2000] or "processor_failed",
                )

            with self.SessionLocal() as db:
                saved = crud.finish_project_operation_item(
                    db,
                    item_id=item.item_id,
                    worker_id=self.worker_id,
                    result=result,
                )
            if saved is None:
                # Lease уже восстановлен другим worker-ом; не перезаписываем
                # его результат и переходим к следующему polling cycle.
                return True
            if result.status == project_operations.PROJECT_OPERATION_STATUS_WAITING_RETRY:
                # При временной недоступности не идём по оставшимся items и не
                # создаём лишние внешние запросы.
                return True

    def recover_expired_leases(self) -> int:
        with self.SessionLocal() as db:
            return crud.recover_expired_project_operation_leases(db)

    def run_forever(self, stop_event: Optional[threading.Event] = None) -> None:
        """Основной цикл; ошибки одного тика не останавливают worker."""
        event = stop_event or threading.Event()
        while not event.is_set():
            try:
                worked = self.run_once()
                if not worked:
                    self.recover_expired_leases()
            except Exception:
                # Следующий тик повторит claim; подробности сохраняются в
                # item/job только после вызова processor.
                pass
            event.wait(self.poll_seconds)


def run_project_operations_worker(
    SessionLocal,
    processor: Processor,
    *,
    worker_id: Optional[str] = None,
    lease_seconds: int = crud.PROJECT_OPERATION_DEFAULT_LEASE_SECONDS,
    poll_seconds: int = 15,
    stop_event: Optional[threading.Event] = None,
) -> None:
    """Совместимый с существующими daemon-thread запуском entrypoint."""
    worker = ProjectOperationsWorker(
        SessionLocal,
        processor,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        poll_seconds=poll_seconds,
    )
    worker.run_forever(stop_event=stop_event)

