"""
Импорт проектов в таблицу projects из enriched JSON.

Назначение:
- Прочитать файл вида project__client_binding_template_enriched.json (поле "items").
- Преобразовать поля из JSON к формату таблицы projects.
- Создать записи в БД (id не задаётся вручную, используется автоинкремент).

Ключевые правила:
- regionMode == null -> region_mode = "include"
- delivery_status -> "На модерации"
- numbers_today -> 0
- numbers_total -> 0
- days_received -> строка вида "Вт. Ср. Чт. Пт. Сб."
- sources_count -> len(sites) + len(phones) + (1 если sms_sender_name задан)
- Проекты с collectionSource="СМС" также импортируются в БД.

Поведение:
- best effort: ошибка по одной записи не останавливает весь импорт.
- подробные логи: [OK] / [SKIP] / [ERR] + итоговая сводка.
- защита от дублей:
  - внутри входного файла (по provider_project_id и name);
  - в БД (по provider_project_id и name).

Запуск:
    python util_12_import_projects_from_enriched_json.py --input project__client_binding_template_enriched.json

Полезные параметры:
    --dry-run            Проверка без записи в БД
    --db-url             Явный DATABASE_URL (иначе берётся из env)
    --allow-name-dup     Разрешить дубли name в БД (по умолчанию пропускаются)
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from backend.app import db as db_mod
from backend.app import models
from backend.app.time_utils import now_msk


DEFAULT_DB_URL = "sqlite:///./app.db"
VALID_STATUSES = {"Активен", "На паузе", "Удалён"}
VALID_COLLECTION_SOURCES = {"Сайты", "Звонки", "СМС", "Ретросайты", "Ретрозвонки", "Пересечение"}
VALID_DATA_SOURCE_CODES = {"B1", "B2", "B3", "B4", "UNMAPPED"}
VALID_DAYS = {"Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"}
DEFAULT_DAYS = ["Вт", "Ср", "Чт", "Пт", "Сб"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Импорт проектов в таблицу projects из enriched JSON")
    parser.add_argument("--input", required=True, help="Путь к enriched JSON-файлу")
    parser.add_argument(
        "--db-url",
        default=os.getenv("DATABASE_URL", DEFAULT_DB_URL),
        help="DATABASE_URL для подключения (по умолчанию из env или sqlite:///./app.db)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Проверка без записи в БД")
    parser.add_argument(
        "--allow-name-dup",
        action="store_true",
        help="Не пропускать записи, если name уже есть в БД",
    )
    return parser.parse_args()


def _as_clean_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _normalize_list(value: Any) -> Optional[List[str]]:
    if value is None:
        return None
    items: List[str] = []
    if isinstance(value, list):
        for raw in value:
            s = _as_clean_str(raw)
            if s:
                items.append(s)
    else:
        s = _as_clean_str(value)
        if s:
            items = [part.strip() for part in s.replace("\n", ",").split(",") if part.strip()]
    if not items:
        return None
    # Сохраняем порядок, убираем дубли.
    seen: Set[str] = set()
    uniq: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        uniq.append(item)
    return uniq or None


def _normalize_days(value: Any) -> List[str]:
    if isinstance(value, list):
        out: List[str] = []
        for raw in value:
            s = _as_clean_str(raw)
            if s and s in VALID_DAYS and s not in out:
                out.append(s)
        if out:
            return out
    return DEFAULT_DAYS.copy()


def _join_days(days: Iterable[str]) -> str:
    return " ".join(f"{d}." for d in days if d)


def _calc_sources_count(
    sites: Optional[List[str]],
    phones: Optional[List[str]],
    sms_sender_name: Optional[str],
) -> int:
    return len(sites or []) + len(phones or []) + (1 if sms_sender_name else 0)


def _get_field(item: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item:
            return item.get(key)
    return None


def _prepare_project_payload(item: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    user_id_raw = _get_field(item, "user_id", "userId")
    user_id = _to_int(user_id_raw)
    if user_id is None:
        return None, f"invalid user_id={user_id_raw!r}"

    provider_project_id = _as_clean_str(_get_field(item, "provider_project_id", "providerProjectId"))
    if not provider_project_id:
        return None, "empty provider_project_id"

    name = _as_clean_str(_get_field(item, "name", "project_name", "projectName"))
    if not name:
        return None, "empty name"

    tag = _as_clean_str(_get_field(item, "tag")) or name

    collection_source = _as_clean_str(_get_field(item, "collectionSource", "collection_source"))
    if collection_source not in VALID_COLLECTION_SOURCES:
        return None, f"invalid collectionSource={collection_source!r}"

    data_source_code = _as_clean_str(_get_field(item, "dataSourceCode", "data_source_code"))
    if data_source_code not in VALID_DATA_SOURCE_CODES:
        return None, f"invalid dataSourceCode={data_source_code!r}"

    status = _as_clean_str(_get_field(item, "status")) or "На паузе"
    if status not in VALID_STATUSES:
        return None, f"invalid status={status!r}"

    data_limit_raw = _get_field(item, "dataLimit", "data_limit")
    data_limit = _to_int(data_limit_raw)
    if data_limit is None:
        return None, f"invalid dataLimit={data_limit_raw!r}"

    region_mode_raw = _as_clean_str(_get_field(item, "regionMode", "region_mode"))
    region_mode = region_mode_raw if region_mode_raw in ("include", "exclude") else "include"

    regions = _normalize_list(_get_field(item, "regions"))
    sites = _normalize_list(_get_field(item, "sites"))
    phones = _normalize_list(_get_field(item, "phones"))
    sms_sender_name = _as_clean_str(_get_field(item, "smsSenderName", "sms_sender_name"))

    days = _normalize_days(_get_field(item, "days"))
    days_received = _join_days(days)
    sources_count = _calc_sources_count(sites, phones, sms_sender_name)

    payload = {
        "user_id": user_id,
        "provider_project_id": provider_project_id,
        "name": name,
        "tag": tag,
        "collection_source": collection_source,
        "data_source_code": data_source_code,
        "region_mode": region_mode,
        "regions": regions,
        "sites": sites,
        "phones": phones,
        "sms_sender_name": sms_sender_name,
        "status": status,
        "delivery_status": "На модерации",
        "data_limit": data_limit,
        "numbers_today": 0,
        "numbers_total": 0,
        "days_received": days_received,
        "sources_count": sources_count,
    }
    return payload, None


def main() -> None:
    load_dotenv()
    args = parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise SystemExit("Некорректный JSON: ожидается объект с массивом items")

    items: List[Any] = data["items"]

    _, session_local = db_mod.init_engine_and_session(args.db_url)
    db_sess = session_local()

    ok = 0
    skipped = 0
    errors = 0

    seen_provider_ids: Set[str] = set()
    seen_names: Set[str] = set()
    users_cache: Dict[int, bool] = {}

    print(f"[INFO] start: items={len(items)}, dry_run={args.dry_run}, allow_name_dup={args.allow_name_dup}")

    try:
        for idx, raw_item in enumerate(items, start=1):
            if not isinstance(raw_item, dict):
                skipped += 1
                print(f"[SKIP] idx={idx}: item is not object")
                continue

            payload, prepare_err = _prepare_project_payload(raw_item)
            if prepare_err:
                skipped += 1
                print(f"[SKIP] idx={idx}: {prepare_err}")
                continue

            assert payload is not None
            provider_project_id = payload["provider_project_id"]
            name = payload["name"]
            user_id = payload["user_id"]

            # Дубли внутри файла.
            if provider_project_id in seen_provider_ids:
                skipped += 1
                print(f"[SKIP] idx={idx} provider_project_id={provider_project_id}: duplicate in input")
                continue
            seen_provider_ids.add(provider_project_id)

            if name in seen_names:
                skipped += 1
                print(f"[SKIP] idx={idx} name={name}: duplicate in input")
                continue
            seen_names.add(name)

            # Проверка существования пользователя.
            user_exists = users_cache.get(user_id)
            if user_exists is None:
                user_exists = db_sess.get(models.User, user_id) is not None
                users_cache[user_id] = user_exists
            if not user_exists:
                skipped += 1
                print(f"[SKIP] idx={idx} name={name}: user_id={user_id} not found")
                continue

            # Дубли в БД.
            db_dup_provider = db_sess.execute(
                select(models.Project.id).where(models.Project.provider_project_id == provider_project_id)
            ).scalar_one_or_none()
            if db_dup_provider is not None:
                skipped += 1
                print(
                    f"[SKIP] idx={idx} name={name}: provider_project_id={provider_project_id} "
                    f"already exists as project_id={db_dup_provider}"
                )
                continue

            if not args.allow_name_dup:
                db_dup_name = db_sess.execute(
                    select(models.Project.id).where(models.Project.name == name)
                ).scalar_one_or_none()
                if db_dup_name is not None:
                    skipped += 1
                    print(f"[SKIP] idx={idx} name={name}: name already exists as project_id={db_dup_name}")
                    continue

            if args.dry_run:
                ok += 1
                print(
                    f"[OK]   idx={idx} DRY-RUN name={name} "
                    f"user_id={user_id} provider_project_id={provider_project_id}"
                )
                continue

            now = now_msk()
            project = models.Project(
                user_id=payload["user_id"],
                provider_project_id=payload["provider_project_id"],
                name=payload["name"],
                tag=payload["tag"],
                collection_source=payload["collection_source"],
                data_source_code=payload["data_source_code"],
                region_mode=payload["region_mode"],
                regions=payload["regions"],
                sites=payload["sites"],
                phones=payload["phones"],
                sms_sender_name=payload["sms_sender_name"],
                status=payload["status"],
                delivery_status=payload["delivery_status"],
                data_limit=payload["data_limit"],
                numbers_today=payload["numbers_today"],
                numbers_total=payload["numbers_total"],
                days_received=payload["days_received"],
                sources_count=payload["sources_count"],
                created_at=now,
                updated_at=now,
            )

            try:
                db_sess.add(project)
                db_sess.commit()
                db_sess.refresh(project)
                ok += 1
                print(
                    f"[OK]   idx={idx} created project_id={project.id} "
                    f"name={project.name} provider_project_id={project.provider_project_id}"
                )
            except SQLAlchemyError as exc:
                db_sess.rollback()
                errors += 1
                print(f"[ERR]  idx={idx} name={name}: DB error: {exc}")
            except Exception as exc:
                db_sess.rollback()
                errors += 1
                print(f"[ERR]  idx={idx} name={name}: unexpected error: {exc}")

    finally:
        db_sess.close()

    print("")
    print("[SUMMARY]")
    print(f"total={len(items)}")
    print(f"ok={ok}")
    print(f"skipped={skipped}")
    print(f"errors={errors}")
    print(f"dry_run={args.dry_run}")


if __name__ == "__main__":
    main()
