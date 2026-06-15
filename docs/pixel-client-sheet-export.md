# Экспорт Пикселя в таблицы клиентов

Скрипт: `tool_export_pixel_leads_to_client_sheets.py`

Назначение: выгружать строки `provider_leads` с `lead_source = "pixel"` напрямую в отдельные Google Sheets клиентов.

## Поля БД

- `client_profiles.pixel_table_url` — полная ссылка на Google Sheets клиента для Пикселя.
- `provider_leads.client_sheet_exported_at` — отметка, что идентификация успешно выгружена в клиентскую таблицу.

Старый экспорт `tool_export_provider_leads.py` остаётся для обычного provider-контура и фильтрует только `lead_source = "provider"`.

## Логика cron-экспорта

Скрипт берёт только строки:

```sql
lead_source = 'pixel'
AND client_sheet_exported_at IS NULL
AND imported_at IS NOT NULL
```

После успешной записи в Google Sheets проставляет `client_sheet_exported_at`.

Если `lk id` уже найден в колонке `R` нужного листа, строка считается уже выгруженной и тоже помечается в БД.

## Листы

Лист выбирается по `provider_leads.imported_at`.

Формат имени листа:

- `Июнь 2026`
- `Июль 2026`
- `Август 2026`

Если листа нет, скрипт создаёт его и копирует первую строку из листа предыдущего месяца. Если листа предыдущего месяца нет, копирует первую строку из первого листа таблицы.

## Колонки A:U

| Колонка | Значение |
|---|---|
| A | `prov_created_at` |
| B | `phone` |
| C | пусто |
| D | пусто |
| E | пусто |
| F | `utm_term` из `pixel_url` |
| G | `utm_source` из `pixel_url` |
| H | `utm_medium` из `pixel_url` |
| I | `utm_campaign` из `pixel_url` |
| J | `utm_content` из `pixel_url` |
| K | `yclid` из `pixel_url` |
| L | `aviclid` из `pixel_url` |
| M | `utm_referrer` из `pixel_url` |
| N | `utm_type` из `pixel_url` |
| O | `utm_domain` из `pixel_url` |
| P | `pixel_url` |
| Q | пусто, резерв под статус/ссылку CRM |
| R | `lk id` (`30100000 + provider_leads.id`) |
| S | `project_name` |
| T | `prov_created_at` |
| U | `imported_at` |

## Env

Используются существующие переменные:

```env
DATABASE_URL=...
GOOGLE_CREDENTIALS_FILE=...
```

Дополнительно опционально:

```env
PIXEL_LEADS_EXPORT_BATCH_LIMIT=1000
```

## Cron

Пример запуска каждые 15 минут:

```cron
CRON_TZ=Europe/Moscow
*/15 * * * * cd /opt/MB_LK_leads && /opt/MB_LK_leads/venv/bin/python tool_export_pixel_leads_to_client_sheets.py
```

Лог:

```text
logs/pixel_client_sheet_export.log
```
