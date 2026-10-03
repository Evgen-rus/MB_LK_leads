from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from backend.app.lead_analytics import db


def test_processing_jobs_are_claimed_in_creation_order(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "analytics.db")
    db.init_db()

    first = db.create_processing_job("run-1", "match", {"project": "First"})
    second = db.create_processing_job("run-2", "analyze", {"project": "Second"})

    claimed_first = db.claim_next_processing_job()
    claimed_second = db.claim_next_processing_job()

    assert claimed_first is not None
    assert claimed_second is not None
    assert claimed_first["id"] == first["id"]
    assert claimed_second["id"] == second["id"]
    assert claimed_first["status"] == "running"


def test_processing_job_progress_and_completion_are_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "analytics.db")
    db.init_db()
    job = db.create_processing_job("run-1", "match", {"project": "First"})

    db.update_processing_job(
        int(job["id"]),
        status="completed",
        phase="Готово",
        processed_rows=50,
        total_rows=50,
        output_file_name="First_сопоставление.xlsx",
    )

    saved = db.get_processing_job(int(job["id"]))
    assert saved["status"] == "completed"
    assert saved["processed_rows"] == 50
    assert saved["total_rows"] == 50
    assert saved["output_file_name"] == "First_сопоставление.xlsx"


def test_startup_fails_interrupted_jobs_and_keeps_queued_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "analytics.db")
    db.init_db()
    interrupted = db.create_processing_job("run-1", "match", {})
    queued = db.create_processing_job("run-2", "analyze", {})
    assert db.claim_next_processing_job()["id"] == interrupted["id"]

    db.fail_interrupted_jobs()

    assert db.get_processing_job(int(interrupted["id"]))["status"] == "failed"
    assert db.get_processing_job(int(queued["id"]))["status"] == "queued"
    assert db.claim_next_processing_job()["id"] == queued["id"]


def test_create_processing_job_atomically_allows_only_one_active_job_per_run(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "analytics.db")
    db.init_db()
    barrier = Barrier(2)

    def enqueue(_kind):
        barrier.wait(timeout=5)
        try:
            return db.create_processing_job("same-run", "match", {})
        except Exception as exc:  # the losing insert is rejected inside the transaction
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(enqueue, ("first", "second")))

    created = [result for result in results if isinstance(result, dict)]
    rejected = [result for result in results if isinstance(result, Exception)]
    assert len(created) == 1
    assert len(rejected) == 1
    assert isinstance(rejected[0], db.sqlite3.IntegrityError)
    assert db.has_active_run_job("same-run")


def test_deferred_job_reserves_run_without_becoming_claimable(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "analytics.db")
    db.init_db()

    reserved = db.create_processing_job("run-1", "analyze", {}, deferred=True)
    assert reserved["status"] == "running"
    assert db.claim_next_processing_job() is None
    with pytest.raises(db.sqlite3.IntegrityError):
        db.create_processing_job("run-1", "match", {})

    db.update_processing_job(int(reserved["id"]), status="queued", phase="В очереди")
    claimed = db.claim_next_processing_job()
    assert claimed is not None
    assert claimed["id"] == reserved["id"]
    assert claimed["status"] == "running"
