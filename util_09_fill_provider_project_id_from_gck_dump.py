"""
Заполняет provider_project_id в project_client_binding_template.json
по точному совпадению имени проекта с выгрузкой gck_projects.

Правила сопоставления:
- project_name == name (точное сравнение "как есть")
- 1 совпадение id  -> записываем provider_project_id
- 0 совпадений     -> оставляем provider_project_id = null
- >1 совпадений    -> оставляем provider_project_id = null и помечаем неоднозначность

Запуск:
    python util_09_fill_provider_project_id_from_gck_dump.py \
      --gck gck_projects_2026-02-24_204224.json \
      --template project_client_binding_template.json

    # Записать в тот же файл:
    python util_09_fill_provider_project_id_from_gck_dump.py \
      --gck gck_projects_2026-02-24_204224.json \
      --template project_client_binding_template.json \
      --in-place
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Заполнить provider_project_id в template-файле по выгрузке gck_projects",
    )
    parser.add_argument(
        "--gck",
        required=True,
        help="Путь к gck_projects_*.json",
    )
    parser.add_argument(
        "--template",
        required=True,
        help="Путь к project_client_binding_template.json",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Куда сохранить результат (по умолчанию: <template>_filled.json)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Перезаписать исходный template-файл",
    )
    return parser.parse_args()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise SystemExit(f"Некорректный JSON-объект: {path}")
    return data


def build_name_to_ids(gck_data: Dict[str, Any]) -> Dict[str, List[str]]:
    result = gck_data.get("result")
    if not isinstance(result, list):
        raise SystemExit("Некорректный gck JSON: поле 'result' должно быть массивом")

    mapping: Dict[str, List[str]] = defaultdict(list)
    for row in result:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        pid = row.get("id")
        if not isinstance(name, str):
            continue
        if pid is None:
            continue
        mapping[name].append(str(pid))
    return mapping


def resolve_out_path(template_path: Path, out_arg: str | None, in_place: bool) -> Path:
    if in_place:
        return template_path
    if out_arg:
        return Path(out_arg)
    return template_path.with_name(f"{template_path.stem}_filled{template_path.suffix}")


def main() -> None:
    args = parse_args()

    gck_path = Path(args.gck)
    template_path = Path(args.template)
    if not gck_path.exists():
        raise SystemExit(f"Файл не найден: {gck_path}")
    if not template_path.exists():
        raise SystemExit(f"Файл не найден: {template_path}")

    gck_data = load_json(gck_path)
    template_data = load_json(template_path)

    items = template_data.get("items")
    if not isinstance(items, list):
        raise SystemExit("Некорректный template JSON: поле 'items' должно быть массивом")

    name_to_ids = build_name_to_ids(gck_data)

    matched = 0
    not_found = 0
    ambiguous = 0

    for item in items:
        if not isinstance(item, dict):
            continue
        project_name = item.get("project_name")
        if not isinstance(project_name, str):
            item["provider_project_id"] = None
            item.pop("provider_project_id_ambiguous_ids", None)
            not_found += 1
            continue

        ids = name_to_ids.get(project_name, [])
        if len(ids) == 1:
            item["provider_project_id"] = ids[0]
            item.pop("provider_project_id_ambiguous_ids", None)
            matched += 1
        elif len(ids) == 0:
            item["provider_project_id"] = None
            item.pop("provider_project_id_ambiguous_ids", None)
            not_found += 1
        else:
            item["provider_project_id"] = None
            item["provider_project_id_ambiguous_ids"] = ids
            ambiguous += 1

    meta = template_data.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        template_data["meta"] = meta

    fill_report = {
        "source_gck_file": str(gck_path),
        "match_mode": "exact_as_is_by_project_name",
        "items_total": len(items),
        "matched": matched,
        "not_found": not_found,
        "ambiguous": ambiguous,
    }
    meta["provider_project_id_fill_report"] = fill_report

    out_path = resolve_out_path(template_path, args.out, args.in_place)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(template_data, f, ensure_ascii=False, indent=2)

    print(f"Готово: {out_path}")
    print(
        "Итог: "
        f"items={len(items)}, "
        f"matched={matched}, "
        f"not_found={not_found}, "
        f"ambiguous={ambiguous}"
    )

    if ambiguous > 0:
        print("Внимание: найдены неоднозначные имена. Для них добавлено поле provider_project_id_ambiguous_ids.")


if __name__ == "__main__":
    main()

