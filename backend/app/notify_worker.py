# Файл: backend/app/notify_worker.py
# Назначение: фоновая отправка в Telegram. Агрегирует изменения за "тихое окно",
# формирует батч-сообщения и отмечает события как отправленные.
# Частота проверки задаётся при запуске (sleep_seconds), окно — DEBOUNCE_WINDOW_MINUTES.

from __future__ import annotations

import math
import time
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Tuple, Optional

from sqlalchemy.orm import Session

from . import models, crud, telegram


def _users_map(s: Session, user_ids: List[int]) -> Dict[int, str]:
    if not user_ids:
        return {}
    rows = s.query(models.User).filter(models.User.id.in_(user_ids)).all()  # type: ignore
    return {u.id: u.login for u in rows}


def _diff_dict(before: dict, after: dict) -> Dict[str, Tuple[object, object]]:
    diff: Dict[str, Tuple[object, object]] = {}
    keys = set(before.keys()) | set(after.keys())
    for k in keys:
        if before.get(k) != after.get(k):
            diff[k] = (before.get(k), after.get(k))
    return diff


def _format_project_brief(d: dict) -> str:
    def _fmt_list(values) -> str:
        vals = values or []
        if not vals:
            return "—"
        # компактный вывод через запятую; если длинно, усечём
        s = ", ".join([str(x) for x in vals])
        if len(s) > 300:
            s = s[:297] + "..."
        return s

    region_mode = d.get('regionMode')
    region_mode_h = "включить" if region_mode == 'include' else ("исключить" if region_mode == 'exclude' else "все")

    parts = []
    parts.append(f"#{d['id']} {d['name']} ({d['dataSourceCode']}, {d['collectionSource']})")
    parts.append(f"  Лимит: {d['dataLimit']}; Дни: {d['daysReceived']}; Режим регионов: {region_mode_h}")
    parts.append(f"  Регионы: {_fmt_list(d.get('regions'))}")
    parts.append(f"  Сайты: {_fmt_list(d.get('sites'))}")
    parts.append(f"  Телефоны: {_fmt_list(d.get('phones'))}")
    parts.append(f"  Статусы: Проект={d['status']}; Отгрузка={d['deliveryStatus']}")
    return "\n".join(parts)


def _format_changes(diff: Dict[str, Tuple[object, object]]) -> List[str]:
    mapping = {
        'dataLimit': 'Лимит',
        'daysReceived': 'Дни',
        'tag': 'Тег',
        'status': 'Статус проекта',
        'regionMode': 'Режим регионов',
        'regions': 'Регионы',
        'sites': 'Сайты',
        'phones': 'Телефоны',
        'smsSenderName': 'СМС отправитель',
        'name': 'Название',
        'collectionSource': 'Источник данных',
        'dataSourceCode': 'Код источника',
        'sourcesCount': 'Источники',
    }
    lines: List[str] = []

    def _human_region_mode(v) -> str:
        if v == 'include':
            return 'include'
        if v == 'exclude':
            return 'exclude'
        return str(v)

    def _fmt_added_removed(b, a, title: str) -> List[str]:
        b_list = b or []
        a_list = a or []
        try:
            b_set = set(b_list)
            a_set = set(a_list)
        except Exception:
            # fallback: только длины
            return [f"  {title}: {len(b_list)} → {len(a_list)}"]
        added = list(a_set - b_set)
        removed = list(b_set - a_set)
        out: List[str] = [f"  {title}: {len(b_list)} → {len(a_list)}"]
        if added:
            add_s = ", ".join([str(x) for x in added[:10]])
            if len(added) > 10:
                add_s += f" и ещё {len(added)-10}"
            out.append(f"    + {add_s}")
        if removed:
            rem_s = ", ".join([str(x) for x in removed[:10]])
            if len(removed) > 10:
                rem_s += f" и ещё {len(removed)-10}"
            out.append(f"    - {rem_s}")
        return out

    for k, (b, a) in diff.items():
        title = mapping.get(k, k)
        if k in ('regions','sites','phones'):
            lines.extend(_fmt_added_removed(b, a, title))
        elif k == 'regionMode':
            lines.append(f"  {title}: {_human_region_mode(b)} → {_human_region_mode(a)}")
        else:
            lines.append(f"  {title}: {b} → {a}")
    return lines


def _chunk_text(text: str, limit: int = 4000) -> List[str]:
    parts: List[str] = []
    while len(text) > limit:
        cut = text.rfind('\n', 0, limit)
        if cut == -1:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:]
    if text:
        parts.append(text)
    return parts


def _build_message(created: List[Tuple[Optional[int], dict]], updated: List[Tuple[Optional[int], dict, dict]], deleted: List[Tuple[Optional[int], dict]], users: Dict[int, str]) -> List[str]:
    lines: List[str] = []
    now = datetime.now().strftime('%H:%M')
    lines.append(f"[ЛК | Клиентские проекты] Изменения за окно (время {now})")
    lines.append("")

    if created:
        lines.append("Создан:")
        for uid, d in created:
            author = users.get(uid or -1, f"user:{uid}") if uid else "неизвестно"
            lines.append(f"От: {author}")
            lines.append(_format_project_brief(d))
        lines.append("")

    if updated:
        lines.append("Изменён:")
        for uid, b, a in updated:
            author = users.get(uid or -1, f"user:{uid}") if uid else "неизвестно"
            lines.append(f"От: {author}")
            lines.append(f"#{a['id']} {a['name']}")
            diff = _format_changes(_diff_dict(b, a))
            for row in diff:
                lines.append(row)
        lines.append("")

    if deleted:
        lines.append("Удалён:")
        for uid, d in deleted:
            author = users.get(uid or -1, f"user:{uid}") if uid else "неизвестно"
            lines.append(f"От: {author}")
            lines.append(f"#{d['id']} {d['name']}")
        lines.append("")

    text = "\n".join(lines).strip()
    return _chunk_text(text)


def run_notifier_loop(SessionLocal, window_minutes: int, bot_token: str, chat_id: str, sleep_seconds: int = 60):
    while True:
        try:
            with SessionLocal() as s:  # type: Session
                state, events = crud.fetch_pending_events(s)
                if not events:
                    pass
                else:
                    user_ids_set = {e.user_id for e in events if getattr(e, "user_id", None) is not None}
                    users = _users_map(s, sorted(list(user_ids_set))) if user_ids_set else {}

                    by_project: Dict[int, List[models.AuditEvent]] = defaultdict(list)
                    blacklist_events: List[models.AuditEvent] = []
                    for ev in events:
                        if ev.project_id is not None:
                            by_project[ev.project_id].append(ev)
                        else:
                            if ev.action in ('blacklist_add', 'blacklist_delete'):
                                blacklist_events.append(ev)

                    created: List[Tuple[Optional[int], dict]] = []
                    updated: List[Tuple[Optional[int], dict, dict]] = []
                    deleted: List[Tuple[Optional[int], dict]] = []

                    for pid, evs in by_project.items():
                        evs.sort(key=lambda e: e.created_at)
                        # If series contains delete, prefer last event's state
                        if evs[-1].action == 'delete':
                            # Use before of last delete to show what was removed
                            snap = evs[-1].before or {}
                            deleted.append((evs[-1].user_id, snap))
                        elif evs[0].action == 'create' and all(e.action != 'delete' for e in evs):
                            # Net result is created; show final after
                            snap = evs[-1].after or {}
                            created.append((evs[-1].user_id, snap))
                        else:
                            # Treat as updated: diff between first.before and last.after
                            b = evs[0].before or (evs[0].after or {})
                            a = evs[-1].after or (evs[-1].before or {})
                            updated.append((evs[-1].user_id, b, a))

                    messages = _build_message(created, updated, deleted, users)

                    # Секция по черному списку: соберём добавленные/удалённые за окно
                    if blacklist_events:
                        add_nums: List[str] = []
                        del_nums: List[str] = []
                        add_authors: List[str] = []
                        del_authors: List[str] = []
                        for ev in blacklist_events:
                            if ev.action == 'blacklist_add' and ev.after:
                                add_nums.extend([str(x) for x in (ev.after.get('phones') or [])])
                                if ev.user_id and (ev.user_id in users):
                                    add_authors.append(users[ev.user_id])
                            elif ev.action == 'blacklist_delete' and ev.before:
                                p = ev.before.get('phone')
                                if p:
                                    del_nums.append(str(p))
                                if ev.user_id and (ev.user_id in users):
                                    del_authors.append(users[ev.user_id])

                        if add_nums or del_nums:
                            now = datetime.now().strftime('%H:%M')
                            lines: List[str] = []
                            lines.append(f"[ЛК | Черный список] Изменения за окно (время {now})")
                            lines.append("")
                            if add_nums:
                                lines.append(f"Добавлены номера ({len(add_nums)}), от: {', '.join(sorted(set(add_authors)) or ['неизвестно'])}:")
                                for n in add_nums:
                                    lines.append(f"{n}")
                                lines.append("")
                            if del_nums:
                                lines.append(f"Удалены номера ({len(del_nums)}), от: {', '.join(sorted(set(del_authors)) or ['неизвестно'])}:")
                                for n in del_nums:
                                    lines.append(f"{n}")
                                lines.append("")
                            bl_msgs = _chunk_text("\n".join(lines).strip())
                            messages.extend(bl_msgs)
                    for msg in messages:
                        telegram.send_text(bot_token, chat_id, msg)

                    crud.mark_events_sent_and_clear(s, events)
        except Exception:
            # Swallow errors; next tick will retry
            pass
        time.sleep(sleep_seconds)


