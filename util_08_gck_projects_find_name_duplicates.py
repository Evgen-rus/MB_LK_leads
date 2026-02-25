"""
Проверка дубликатов проектов в выгрузке gck_projects.

Что делает:
- Читает JSON-файл с форматом ответа команды gck_projects.
- Ищет дубли по полю `name` в секции `result`.
- Печатает название и список `id` для дублей.

Запуск:
    python util_08_gck_projects_find_name_duplicates.py gck_projects_2026-02-24_204224.json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Найти дубли по name в gck_projects JSON")
    parser.add_argument("file", help="Путь к файлу выгрузки gck_projects_*.json")
    return parser.parse_args()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    args = parse_args()
    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"Файл не найден: {path}")

    data = load_json(path)
    result = data.get("result")
    if not isinstance(result, list):
        raise SystemExit("Некорректный формат: ожидается массив в поле 'result'")

    by_name: Dict[str, List[str]] = defaultdict(list)
    for row in result:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        project_id = row.get("id")
        if not isinstance(name, str):
            continue
        by_name[name].append("" if project_id is None else str(project_id))

    duplicates = {name: ids for name, ids in by_name.items() if len(ids) > 1}

    print(f"Файл: {path}")
    print(f"Всего записей в result: {len(result)}")
    print(f"Уникальных name: {len(by_name)}")
    print(f"Дублей по name: {len(duplicates)}")

    if not duplicates:
        print("Дубликаты не найдены.")
        return

    print("\nСписок дублей:")
    for name in sorted(duplicates.keys()):
        ids = ", ".join(duplicates[name])
        print(f"- {name} -> {ids}")


if __name__ == "__main__":
    main()

