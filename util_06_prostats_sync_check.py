"""
Берёт все не удалённые проекты из нашей БД.
Для каждого с provider_project_id запрашивает проект у провайдера.
Сравнивает:
- type, src
- status
- limit
- список сайтов/телефонов (для hosts/calls)
Печатает только ошибки и итоговую статистику.

Запуск:
python util_06_prostats_sync_check.py
"""

from __future__ import annotations

import os
from typing import List, Optional

from dotenv import load_dotenv

from backend.app import db, models
from backend.app.providers import prostats


def normalize_list(items: Optional[List[str]]) -> List[str]:
    if not items:
        return []
    return sorted({str(x).strip() for x in items if x and str(x).strip()})


def parse_provider_list(content: str) -> List[str]:
    parts = [s.strip() for s in str(content or "").split(",") if s.strip()]
    return sorted(set(parts))


def to_int(value: object) -> Optional[int]:
    try:
        return int(str(value).strip())
    except Exception:
        return None


def parse_regions_value(value: object) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return normalize_list(value)
    raw = str(value).strip()
    if not raw:
        return []
    parts = [s.strip() for s in raw.replace(";", ",").split(",") if s.strip()]
    return normalize_list(parts)


def compare_project(project: models.Project, detail: dict) -> List[str]:
    issues: List[str] = []

    expected_type = prostats._type_from_collection(project.collection_source)
    expected_src = prostats._src_from_code(project.data_source_code)

    provider_type = str(detail.get("type") or "").strip()
    provider_src = str(detail.get("src") or "").strip()

    if provider_type and provider_type != expected_type:
        issues.append(f"Тип: провайдер={provider_type}, локально={expected_type}")
    if provider_src and provider_src != expected_src:
        issues.append(f"Источник (src): провайдер={provider_src}, локально={expected_src}")

    provider_status = to_int(detail.get("status"))
    local_status = 1 if project.status == "Активен" else 0
    if provider_status is not None and provider_status != local_status:
        issues.append(f"Статус: провайдер={provider_status}, локально={local_status}")

    provider_limit = to_int(detail.get("limit"))
    if provider_limit is not None and provider_limit != project.data_limit:
        issues.append(f"Лимит: провайдер={provider_limit}, локально={project.data_limit}")

    if expected_type in ("hosts", "calls"):
        provider_items = parse_provider_list(detail.get("content") or "")
        if expected_type == "hosts":
            local_items = normalize_list(project.sites or [])
        else:
            local_items = normalize_list(project.phones or [])

        missing = sorted(set(local_items) - set(provider_items))
        extra = sorted(set(provider_items) - set(local_items))
        if missing:
            issues.append(f"Нет у провайдера: {', '.join(missing)}")
        if extra:
            issues.append(f"Лишнее у провайдера: {', '.join(extra)}")

    local_regions = normalize_list(project.regions or [])
    provider_regions = parse_regions_value(detail.get("regions"))
    if local_regions or provider_regions:
        if local_regions != provider_regions:
            issues.append(
                "Регионы: "
                f"провайдер={','.join(provider_regions) or 'пусто'}, "
                f"локально={','.join(local_regions) or 'пусто'}",
            )

        local_reverse = True if project.region_mode == "exclude" else False
        provider_reverse_raw = detail.get("regions_reverse")
        if provider_reverse_raw is None:
            issues.append(
                "Режим регионов: провайдер=неизвестно, "
                f"локально={'exclude' if local_reverse else 'include'}",
            )
        else:
            provider_reverse = bool(provider_reverse_raw)
            if local_reverse != provider_reverse:
                issues.append(
                    "Режим регионов: "
                    f"провайдер={'exclude' if provider_reverse else 'include'}, "
                    f"локально={'exclude' if local_reverse else 'include'}",
                )

    return issues


def main() -> None:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL", "sqlite:///./app.db")
    _, session_local = db.init_engine_and_session(database_url)
    db_sess = session_local()

    checked = 0
    ok = 0
    failed = 0

    try:
        projects = (
            db_sess.query(models.Project)
            .filter(models.Project.status != "Удалён")
            .all()
        )
        for project in projects:
            if not project.provider_project_id:
                print(f"SKIP id={project.id} name={project.name} (нет provider_project_id)")
                continue

            checked += 1
            try:
                detail = prostats._get_project(str(project.provider_project_id))
            except prostats.ProstatsError as exc:
                failed += 1
                print(
                    f"ОШИБКА id={project.id} name={project.name} "
                    f"provider_id={project.provider_project_id}: {exc.message}",
                )
                continue

            if not detail:
                failed += 1
                print(
                    f"ОШИБКА id={project.id} name={project.name} "
                    f"provider_id={project.provider_project_id}: проект у провайдера не найден",
                )
                continue

            issues = compare_project(project, detail)
            if issues:
                failed += 1
                header = (
                    f"ОШИБКА id={project.id} name={project.name} "
                    f"provider_id={project.provider_project_id}"
                )
                print(header)
                for issue in issues:
                    print(f"  - {issue}")
            else:
                ok += 1

        print(f"\nПроверено: {checked}, ОК: {ok}, Ошибок: {failed}")
    finally:
        db_sess.close()


if __name__ == "__main__":
    main()
