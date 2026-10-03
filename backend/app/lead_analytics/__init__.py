"""Lead Analytics embedded in the personal cabinet backend."""

from . import db


def init_storage() -> None:
    from .config import ensure_dirs

    ensure_dirs()
    db.init_db()


def startup() -> None:
    init_storage()
    db.fail_interrupted_jobs()
    from .router import start_worker

    start_worker()
