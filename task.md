## Что сделано
- Добавил модель `ProviderLead` с полями, включая `subdomain` и `phones_raw`.
- Настроил отдельный лог `logs/provider_webhook.log` с суточной ротацией и хранением 30 дней (как `app.log`).
- Добавил эндпоинт вебхука `/api/provider-test/{WEBHOOK_SECRET}`.
- Дедупликация по `vid`: если такой `vid` уже есть, запись не создаётся.
- Сопоставление `project_id` по `projects.name == project_name` (как ты просил).

## Важные детали логики
- `phone` пишется строкой: если телефонов несколько — через запятую.  
- `phones_raw` хранит исходный массив телефонов (на будущее).
- `prov_chanel` берётся из начала `page` до первого `_` (например `B2`).
- `prov_source` — часть после **второго** `_` (если есть).
- `prov_created_at` — из `time` (timestamp → MSK).
- `imported_at` — MSK (когда записали в БД).
- `subdomain` сохраняем, если есть.

## Где изменения
- `backend/app/models.py` — модель `ProviderLead`
- `backend/app/logging_setup.py` — ротация `provider_webhook.log`
- `backend/app/main.py` — эндпоинт вебхука и парсинг
- `backend/app/crud.py` — CRUD для `provider_leads`

## Важный риск (средний)
Сопоставление `project_id` по точному совпадению `projects.name == page`.  
Если в БД имена отличаются от строк `page`, `project_id` будет `NULL`.  
Если нужно — сделаю более гибкое сопоставление (например, по части строки).

Если хочешь, дальше могу:
- добавить вывод `provider_leads` во фронт,
- сделать отдельный экспорт,
- добавить схему в `schemas.py` и API для просмотра.