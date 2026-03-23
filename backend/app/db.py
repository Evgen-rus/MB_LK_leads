"""
Файл: backend/app/db.py
Назначение: инициализация SQLAlchemy engine и фабрики сессий (SessionLocal).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def init_engine_and_session(
    database_url: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_timeout: int = 30,
    pool_recycle: int = 1800,
):
    connect_args = {}
    engine_kwargs = {"future": True}
    if database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    else:
        engine_kwargs.update(
            {
                "pool_size": max(1, int(pool_size)),
                "max_overflow": max(0, int(max_overflow)),
                "pool_timeout": max(1, int(pool_timeout)),
                "pool_recycle": max(30, int(pool_recycle)),
                "pool_pre_ping": True,
            }
        )
    engine = create_engine(database_url, connect_args=connect_args, **engine_kwargs)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
    return engine, SessionLocal


