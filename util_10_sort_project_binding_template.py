"""
Сортировка project_client_binding_template JSON по project_name.

Правила:
- Группировка по "базовому" имени проекта без префикса Bn_.
  Пример: B1_[LR122] Автоград Татьяна -> база "[LR122] Автоград Татьяна".
- Внутри одной базы порядок:
  B1, B2, B3, B4, затем остальные Bn (например B6), затем всё прочее.
- Сравнение имён выполняется "как есть", без нормализации содержимого.

Запуск:
    python util_10_sort_project_binding_template.py --file project_client_binding_template_filled.json
    python util_10_sort_project_binding_template.py --file project_client_binding_template.json --in-place
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Tuple


B_PREFIX_RE = re.compile(r"^B(\d+)_(.*)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Сортировать template JSON: B1/B2/B3/B4 внутри одинаковых базовых названий",
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Путь к JSON-файлу шаблона",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Куда сохранить результат (по умолчанию: <file>_sorted.json)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Перезаписать исходный файл",
    )
    return parser.parse_args()


def resolve_out_path(src: Path, out_arg: str | None, in_place: bool) -> Path:
    if in_place:
        return src
    if out_arg:
        return Path(out_arg)
    return src.with_name(f"{src.stem}_sorted{src.suffix}")


def project_sort_key(item: Dict[str, Any]) -> Tuple[str, int, int, str]:
    name = item.get("project_name")
    if not isinstance(name, str):
        # Невалидные/пустые названия отправляем в конец.
        return ("", 2, 10_000, "")

    m = B_PREFIX_RE.match(name)
    if not m:
        # Без префикса Bn_ - в конец своей группы.
        return (name.casefold(), 1, 10_000, name.casefold())

    b_num = int(m.group(1))
    base_name = m.group(2)

    # Явный приоритет для B1..B4, остальные Bn после них.
    if b_num in (1, 2, 3, 4):
        b_rank = b_num
    else:
        b_rank = 100 + b_num

    return (base_name.casefold(), 0, b_rank, name.casefold())


def main() -> None:
    args = parse_args()
    src = Path(args.file)
    if not src.exists():
        raise SystemExit(f"Файл не найден: {src}")

    with src.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise SystemExit("Некорректный формат: ожидается JSON-объект с полем items")

    items = data.get("items")
    if not isinstance(items, list):
        raise SystemExit("Некорректный формат: поле 'items' должно быть массивом")

    # Сортируем копию массива.
    sorted_items = sorted(
        (it for it in items if isinstance(it, dict)),
        key=project_sort_key,
    )

    # Если в items встретятся не-объекты, аккуратно добавим их в конец без изменений.
    tail_non_dict = [it for it in items if not isinstance(it, dict)]
    data["items"] = sorted_items + tail_non_dict

    out_path = resolve_out_path(src, args.out, args.in_place)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Готово: {out_path}")
    print(f"Всего items: {len(items)}")
    print("Порядок внутри группы: B1 -> B2 -> B3 -> B4 -> Bn(прочие)")


if __name__ == "__main__":
    main()

