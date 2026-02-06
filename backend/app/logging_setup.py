"""
Файл: backend/app/logging_setup.py
Назначение: настройка логов — файл logs/app.log с суточной ротацией, хранение 30 дней.
"""
import logging
import os
from logging.handlers import TimedRotatingFileHandler


LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', 'logs')
LOG_DIR = os.path.abspath(LOG_DIR)


def ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)


def setup_logging():
    ensure_log_dir()
    log_path = os.path.join(LOG_DIR, 'app.log')

    # Раз в сутки, в полночь (по системному времени), хранить 30 файлов
    file_handler = TimedRotatingFileHandler(
        filename=log_path,
        when='midnight',
        interval=1,
        backupCount=30,
        encoding='utf-8',
        utc=False,
    )
    formatter = logging.Formatter(
        fmt='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console)

    # Шумы от uvicorn можно приглушить/перенаправить при желании
    logging.getLogger('uvicorn').setLevel(logging.INFO)
    for name in ('uvicorn', 'uvicorn.error', 'uvicorn.access'):
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        lg.addHandler(file_handler)


def setup_provider_webhook_logger() -> logging.Logger:
    ensure_log_dir()
    log_path = os.path.join(LOG_DIR, 'provider_webhook.log')

    logger = logging.getLogger("provider.webhook")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    for handler in logger.handlers:
        if isinstance(handler, TimedRotatingFileHandler) and handler.baseFilename == log_path:
            return logger

    file_handler = TimedRotatingFileHandler(
        filename=log_path,
        when='midnight',
        interval=1,
        backupCount=30,
        encoding='utf-8',
        utc=False,
    )
    formatter = logging.Formatter(
        fmt='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    logger.addHandler(file_handler)

    return logger


def setup_provider_export_logger() -> logging.Logger:
    ensure_log_dir()
    log_path = os.path.join(LOG_DIR, 'provider_export.log')

    logger = logging.getLogger("provider.export")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    for handler in logger.handlers:
        if isinstance(handler, TimedRotatingFileHandler) and handler.baseFilename == log_path:
            return logger

    file_handler = TimedRotatingFileHandler(
        filename=log_path,
        when='midnight',
        interval=1,
        backupCount=10,
        encoding='utf-8',
        utc=False,
    )
    formatter = logging.Formatter(
        fmt='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    logger.addHandler(file_handler)

    return logger


