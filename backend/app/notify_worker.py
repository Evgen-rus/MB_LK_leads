# Файл: backend/app/notify_worker.py
# Назначение: фоновый воркер "тихого окна" для событий из audit_events.
#
# Исторически этот воркер:
# - собирал изменения проектов и чёрного списка за debounce-окно;
# - отправлял батч-сводку в Telegram;
# - помечал события как отправленные.
#
# С 2026-03-10 Telegram-сводки по audit_events больше не используются:
# - создание/изменение/удаление проектов отключено;
# - добавление/удаление номеров ЧС отключено.
#
# При этом сам воркер оставляем:
# - чтобы не копить бесконечную очередь audit_events со статусом sent=False;
# - чтобы сохранить debounce-механику на будущее;
# - чтобы при необходимости можно было вернуть Telegram-батчи в одном месте.

from __future__ import annotations

import time

from sqlalchemy.orm import Session

from . import crud


def run_notifier_loop(SessionLocal, window_minutes: int, bot_token: str, chat_id: str, sleep_seconds: int = 60):
    # Аргументы window_minutes / bot_token / chat_id намеренно оставлены в сигнатуре.
    # Так не нужно менять код запуска воркера в main.py, и проще вернуть отправку позже.
    _ = window_minutes, bot_token, chat_id

    while True:
        try:
            with SessionLocal() as s:  # type: Session
                _state, events = crud.fetch_pending_events(s)
                if events:
                    # Важно:
                    # события audit_events по-прежнему нужны для:
                    # - истории в интерфейсе клиента;
                    # - админского контроля изменений;
                    # - возможного будущего возврата Telegram-сводок.
                    #
                    # Но в Telegram их больше не отправляем.
                    # Вместо этого просто закрываем текущую debounce-очередь.
                    crud.mark_events_sent_and_clear(s, events)
        except Exception:
            # Ошибки не должны останавливать фоновый цикл:
            # на следующем тике воркер попробует снова.
            pass
        time.sleep(sleep_seconds)


