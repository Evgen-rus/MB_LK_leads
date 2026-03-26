# Release Notes — 26.03.2026

## Кратко

- Сокращено время жизни DB-сессий вокруг внешних вызовов `Prostats` и `Telegram`.
- Основные user/admin project flows переведены на схему: snapshot данных -> внешний вызов -> короткая новая DB-сессия для записи результата.
- Массовые admin-операции `pause/resume` больше не держат request-scoped сессию во время сетевых вызовов.
- Отдельно упрощён `admin_update_client`: Telegram test send теперь отделён от реального сохранения клиента.

- Добавлен отдельный CLI-скрипт recovery-дозагрузки потерянных лидов из `gck`: `tool_recover_provider_leads_from_gck_phones.py`. Пока тестирую его в ручную, далее запуск по расписанию сделаю

## Что изменено

### Backend

- Добавлены вспомогательные обёртки для коротких write/finalize-сессий после внешних вызовов.
- В `create_projects`, `update_project`, `delete_project` и admin-эквивалентах внешний `Prostats` вызывается уже вне длинной request-scoped транзакции.
- В `admin_pause_client_projects` и `admin_resume_client_projects` локальные обновления статусов, snapshot и debounce-финализация вынесены в отдельные короткие сессии.
- В `admin_update_client` убран промежуточный черновой апдейт в request-сессии:
  - сначала выполняется dry-run validation в отдельной сессии;
  - затем при необходимости отправляется test message в Telegram;
  - затем выполняется один реальный commit в новой сессии.
- Добавлен отдельный recovery-tool `tool_recover_provider_leads_from_gck_phones.py`:
  - скрипт независимо от `tool_compare_provider_api_vs_db.py` опрашивает API провайдера `gck_phones`;
  - сравнивает данные с `provider_leads` по правилу `project + phone + day`;
  - повторные появления одного и того же телефона в одном проекте за день сознательно считаются одним событием;
  - при нахождении missing-лида может дозаписать строку в `provider_leads` с synthetic recovery `vid`;
  - для проверки без записи есть режим `--dry-run`.

### Provider Leads Recovery

- Формат recovery `vid` выбран numeric-only, чтобы не ломать downstream-цепочку через Google Sheets:
  - схема `YMMDDNNNNN`;
  - пример: `6032500002` = recovery-лид за `2026-03-25`, sequence `00002`.
- Recovery-скрипт не изменяет webhook-логику и не встраивается в runtime-path приёма лидов.
- DB-нагрузка у recovery-скрипта умеренная:
  - сначала короткая read-сессия для загрузки проектов и batched-read по `provider_leads`;
  - затем сетевые вызовы к API провайдера уже вне длинной DB-сессии;
  - запись missing-лидов выполняется короткими отдельными write-сессиями.

### Telegram / Provider Integrations

- Telegram support message и ambiguity-notification по webhook больше не выполняются на фоне живой request-scoped DB-сессии.
- Для вызовов `Prostats` используются snapshot-данные проекта, чтобы не тащить ORM-объекты через границу внешнего вызова.

## Поведение системы

- Бизнес-логика пользовательских и админских сценариев не меняется:
  - проекты по-прежнему создаются, обновляются, удаляются и паузятся в тех же точках;
  - Telegram test send при настройке клиента по-прежнему блокирует сохранение, если сообщение не отправилось.
- Изменение касается только границ транзакций и времени удержания DB connection.
- Recovery потерянных лидов остаётся отдельной служебной CLI-задачей:
  - не заменяет дедупликацию webhook по `vid`;
  - не пытается reconcile recovery-лид с поздним webhook;
  - ориентирован на практический сценарий периодической дозагрузки потеряшек.

## Зачем это нужно

- Снижает риск `pool timeout` при медленных внешних вызовах.
- Уменьшает вероятность зависших long-lived транзакций в request flow.
- Делает connection budget предсказуемее без усложнения архитектуры и без внедрения PgBouncer на текущем этапе.
