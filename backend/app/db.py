"""
Файл: backend/app/db.py
Назначение: инициализация SQLAlchemy engine и фабрики сессий (SessionLocal).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def init_engine_and_session(database_url: str):
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    engine = create_engine(database_url, connect_args=connect_args, future=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
    return engine, SessionLocal


