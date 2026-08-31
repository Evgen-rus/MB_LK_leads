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
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Callable, Optional, Union

from . import crud, project_operations


Processor = Union[
    project_operations.ProjectOperationProcessor,
    Callable[[project_operations.ProjectOperationItemContext], project_operations.ProjectOperationItemResult],
]


PROJECT_OPERATION_DEFAULT_CONCURRENCY = 3
PROJECT_OPERATION_MIN_CONCURRENCY = 1
PROJECT_OPERATION_MAX_CONCURRENCY = 4
PROJECT_OPERATION_SAFE_LEASE_SECONDS = 600

# Only these values are known provider-backed collection contours.  Keeping
# the allow-list here is deliberate: an unknown/legacy source must remain
# sequential until its semantics are confirmed.
_PARALLEL_PROVIDER_COLLECTION_SOURCES = frozenset(
    {
        "Сайты",
        "Звонки",
        "Ретросайты",
        "Ретрозвонки",
        "Пересечение",
    }
)


def bounded_project_operation_concurrency(value: int) -> int:
    """Clamp the worker concurrency to the deliberately small safe range."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = PROJECT_OPERATION_DEFAULT_CONCURRENCY
    return max(PROJECT_OPERATION_MIN_CONCURRENCY, min(PROJECT_OPERATION_MAX_CONCURRENCY, parsed))


def is_parallelizable_project_operation_item(
    item: project_operations.ProjectOperationItemContext,
) -> bool:
    """Return True only for independent, provider-backed status items.

    Client-wide pause/resume uses the same provider status contract; its
    shared snapshot is serialized separately with a PostgreSQL row lock. SMS
    and Pixel have their own mechanics and must never enter this pool.
    """
    payload = item.payload_snapshot if isinstance(item.payload_snapshot, dict) else {}
    state = item.state_snapshot if isinstance(item.state_snapshot, dict) else {}
    if str(payload.get("action") or "").strip() != "status":
        return False
    desired_status = str(payload.get("status") or "").strip()
    if desired_status not in {"Активен", "На паузе"}:
        return False
    # Only jobs created by the new launcher carry this explicit marker.
    # In-flight legacy jobs remain serial after a deployment, including an
    # old collection pause that may not yet have a pre-created snapshot row.
    if payload.get("parallelSafe") is not True:
        return False
    if not str(item.provider_project_id or "").strip():
        return False
    source = str(
        state.get("collection_source", state.get("collectionSource", "")) or ""
    ).strip()
    return source in _PARALLEL_PROVIDER_COLLECTION_SOURCES


def is_retryable_project_operation_result(
    result: project_operations.ProjectOperationItemResult,
) -> bool:
    """Recognize retry/transport outcomes that must stop a new batch."""
    if result.status == project_operations.PROJECT_OPERATION_STATUS_WAITING_RETRY:
        return True
    error = str(result.error or "").strip().lower()
    # Adapters normally map these failures to waiting_retry.  The marker
    # fallback keeps the worker conservative if a processor returns a custom
    # transport result as needs_attention.
    return any(
        marker in error
        for marker in (
            "transport",
            "timeout",
            "timed out",
            "временно недоступ",
        )
    )


class ProjectOperationsWorker:
    """Durable project-operation worker with a small safe status-only pool."""

    def __init__(
        self,
        SessionLocal,
        processor: Processor,
        *,
        worker_id: Optional[str] = None,
        lease_seconds: int = crud.PROJECT_OPERATION_DEFAULT_LEASE_SECONDS,
        poll_seconds: int = 15,
        concurrency: int = PROJECT_OPERATION_DEFAULT_CONCURRENCY,
    ):
        if processor is None:
            raise ValueError("processor is required")
        self.SessionLocal = SessionLocal
        self.processor = processor
        self.worker_id = str(worker_id or "").strip() or f"project-operations-{uuid.uuid4().hex[:12]}"
        self.lease_seconds = max(
            PROJECT_OPERATION_SAFE_LEASE_SECONDS,
            crud._bounded_project_operation_lease_seconds(lease_seconds),
        )
        self.poll_seconds = max(1, int(poll_seconds or 15))
        self.concurrency = bounded_project_operation_concurrency(concurrency)

    def _process_item(
        self,
        item: project_operations.ProjectOperationItemContext,
    ) -> project_operations.ProjectOperationItemResult:
        processor_method = getattr(self.processor, "process_item", None)
        result = processor_method(item) if callable(processor_method) else self.processor(item)  # type: ignore[operator]
        if not isinstance(result, project_operations.ProjectOperationItemResult):
            raise TypeError("project operation processor must return ProjectOperationItemResult")
        return result

    def _process_item_result(
        self,
        item: project_operations.ProjectOperationItemContext,
    ) -> tuple[project_operations.ProjectOperationItemContext, project_operations.ProjectOperationItemResult]:
        """Process one detached context without retaining a DB session."""
        try:
            result = self._process_item(item)
        except Exception as exc:  # noqa: BLE001 - one item must not kill worker
            result = project_operations.ProjectOperationItemResult.waiting_retry(
                error=str(exc)[:2000] or "processor_failed",
            )
        return item, result

    def _finish_item(
        self,
        item: project_operations.ProjectOperationItemContext,
        result: project_operations.ProjectOperationItemResult,
    ) -> bool:
        """Finish an item in a short, independent DB session."""
        with self.SessionLocal() as db:
            saved = crud.finish_project_operation_item(
                db,
                item_id=item.item_id,
                worker_id=self.worker_id,
                result=result,
            )
        return saved is not None

    def _database_supports_parallel_claim(self, db=None) -> bool:
        """Use the pool only with PostgreSQL row-lock semantics.

        SQLite remains a supported development fallback, but its locking
        behaviour is not suitable for concurrent item finishing.  Returning
        False keeps local development on the original reliable path.
        """
        try:
            if db is not None:
                bind = db.get_bind()
                return str(getattr(getattr(bind, "dialect", None), "name", "")).lower() == "postgresql"
            with self.SessionLocal() as probe_db:
                bind = probe_db.get_bind()
                return str(getattr(getattr(bind, "dialect", None), "name", "")).lower() == "postgresql"
        except Exception:
            return False

    def _process_batch(
        self,
        items: list[project_operations.ProjectOperationItemContext],
    ) -> bool:
        """Process an already-claimed batch; return whether retry mode fired."""
        if len(items) == 1:
            item, result = self._process_item_result(items[0])
            self._finish_item(item, result)
            return is_retryable_project_operation_result(result)

        results: list[tuple[project_operations.ProjectOperationItemContext, project_operations.ProjectOperationItemResult]] = []
        with ThreadPoolExecutor(max_workers=len(items), thread_name_prefix="project-operation") as pool:
            futures: dict[Future[tuple[project_operations.ProjectOperationItemContext, project_operations.ProjectOperationItemResult]], project_operations.ProjectOperationItemContext] = {
                pool.submit(self._process_item_result, item): item for item in items
            }
            for future in as_completed(futures):
                # Processor calls may run concurrently, but item finalization
                # is deliberately serialized.  This avoids races while each
                # finish recalculates the shared job counters/status.
                results.append(future.result())
        retry_seen = False
        for item, result in results:
            saved = self._finish_item(item, result)
            if not saved:
                retry_seen = True
            retry_seen = retry_seen or is_retryable_project_operation_result(result)
        return retry_seen

    def run_once(self) -> bool:
        """Обработать доступную job; вернуть True, если работа была найдена."""
        retry_mode = False
        with self.SessionLocal() as db:
            operation = crud.claim_project_operation(
                db,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
            if operation is not None:
                retry_mode = crud.project_operation_has_waiting_retry_items(
                    db,
                    operation_id=operation.id,
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
                if not retry_mode and self.concurrency > 1 and self._database_supports_parallel_claim(db):
                    items = crud.claim_project_operation_item_batch(
                        db,
                        operation_id=operation.id,
                        worker_id=self.worker_id,
                        lease_seconds=self.lease_seconds,
                        limit=self.concurrency,
                        predicate=is_parallelizable_project_operation_item,
                    )
                    # A job may contain only update/delete/SMS/Pixel or
                    # legacy items.  The status-only batch is intentionally
                    # empty for those, so immediately use the original
                    # single-item path instead of leaving the job running.
                    if not items:
                        item = crud.claim_next_project_operation_item(
                            db,
                            operation_id=operation.id,
                            worker_id=self.worker_id,
                            lease_seconds=self.lease_seconds,
                        )
                        items = [item] if item is not None else []
                else:
                    item = crud.claim_next_project_operation_item(
                        db,
                        operation_id=operation.id,
                        worker_id=self.worker_id,
                        lease_seconds=self.lease_seconds,
                    )
                    items = [item] if item is not None else []
            if not items:
                return True

            retry_seen = self._process_batch(items)
            if retry_seen:
                # Already-claimed siblings finish, but no fresh batch is
                # claimed.  The next run sees waiting_retry in PostgreSQL and
                # remains serial for the entire conservative retry attempt.
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
    concurrency: int = PROJECT_OPERATION_DEFAULT_CONCURRENCY,
    stop_event: Optional[threading.Event] = None,
) -> None:
    """Совместимый с существующими daemon-thread запуском entrypoint."""
    worker = ProjectOperationsWorker(
        SessionLocal,
        processor,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        poll_seconds=poll_seconds,
        concurrency=concurrency,
    )
    worker.run_forever(stop_event=stop_event)
