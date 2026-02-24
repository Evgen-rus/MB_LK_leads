# Фактическое обследование по теме привязки проектов к клиентам

Дата фиксации: 2026-02-24  
Обследованные источники: `ARCHITECTURE.md`, `backend/app/*.py`, `backend/app/providers/prostats.py`, `my-app-vite/src/api.ts`, `my-app-vite/src/components/*.tsx`, `tool_export_provider_leads.py`, `README.md`.

## 1) Архитектура проекта (факты)

- Backend: FastAPI (`backend/app/main.py`).
- Frontend: React + Vite (`my-app-vite/src`).
- БД: SQLAlchemy; `DATABASE_URL` берётся из env, fallback `sqlite:///./app.db` (`backend/app/main.py`, `backend/app/db.py`).
- На старте backend:
  - загружает `.env`;
  - требует `WEBHOOK_SECRET` (без него `RuntimeError`);
  - вызывает `models.Base.metadata.create_all(bind=engine)`;
  - запускает фоновый поток `notify_worker`.
- Админ определяется проверкой `user.id == 1` (`require_admin` в `backend/app/main.py`).
- Таймзона вычисляется через `SHEETS_TZ` (fallback `Europe/Moscow` / UTC+3) и используется в фильтрах дат (`backend/app/time_utils.py`, `backend/app/main.py`).

## 2) Доменные модели и текущие паттерны (факты)

### Основные таблицы/модели

- `users` (`models.User`)
- `client_profiles` (`models.ClientProfile`)
- `projects` (`models.Project`)
- `provider_leads` (`models.ProviderLead`)
- `audit_events` (`models.AuditEvent`)
- `client_project_pause_snapshots` (`models.ClientProjectPauseSnapshot`)
- `client_balance_operations` (`models.ClientBalanceOperation`)
- `blacklist_phones` (`models.BlacklistPhone`)
- `report_exports` (`models.ReportExport`)
- `project_id_map` (`models.ProjectIdMap`)

### Связь клиента и проекта

- Владелец проекта хранится в `projects.user_id` (`ForeignKey("users.id")`).
- Профиль клиента хранится отдельно в `client_profiles.user_id` (`ForeignKey("users.id")`, unique по `user_id`).
- Поле связи проекта с внешним провайдером: `projects.provider_project_id`.

### Паттерн операций с проектами

- Создание проекта (`POST /projects`) сначала вызывает Prostats (`prostats.create_project`), потом сохраняет локально через `crud.create_projects`.
- В `crud.create_projects` проекту присваивается `user_id` текущего пользователя и `provider_project_id` из ответа Prostats.
- Обновление/удаление проекта (`PATCH/DELETE /projects/{id}` и админские аналоги) требуют непустой `provider_project_id`; иначе возвращается `409`.
- Удаление проекта реализовано как soft-delete: `projects.status = 'Удалён'`.
- Изменения пишутся в `audit_events` (action `create/update/delete` и др.), с `actor_user_id` и `via_impersonation`.

### Паттерн ролей/доступа

- Клиентские выборки проектов фильтруются по `projects.user_id == current_user.id`.
- Админские выборки проектов доступны через `/admin/projects` с фильтром `userId`.
- Эндпоинтов, которые принимают `client_id/user_id` для смены владельца существующего проекта, в `main.py` нет.

### Наблюдение по `project_id_map`

- Модель `ProjectIdMap` объявлена в `backend/app/models.py`.
- Поиск по репозиторию не показывает обращений к `ProjectIdMap/project_id_map` вне `models.py`.

## 3) Внешние интеграции и текущая работа (факты)

### Prostats

- Интеграция реализована в `backend/app/providers/prostats.py`.
- Используется HTTP `POST` (библиотека `requests`) на `PROSTATS_API_URL` (default `https://prostats.info/api/index.php`).
- Токен берётся из `PROSTATS_TOKEN`; при пустом токене выбрасывается `ProstatsError`.
- Используемые команды:
  - `gck_project_create`
  - `gck_project_update`
  - `gck_project_delete`
  - `gck_project`
  - `gck_projects`
- В runtime backend Prostats вызывается при create/update/delete проекта и при массовых pause/resume для клиента.

### Provider webhook

- Endpoint: `POST /api/provider-test/{secret}`.
- Секрет проверяется с `WEBHOOK_SECRET`.
- Входящий payload логируется в `logs/provider_webhook.log`.
- Дедупликация лидов выполняется по `provider_leads.vid` (unique + проверка в `crud.get_provider_lead_by_vid`).
- `project_id` лида определяется по точному совпадению `projects.name == payload.page` (`crud.get_project_id_by_name`).

### Telegram

- Отправка: `backend/app/telegram.py`, вызов Telegram Bot API `sendMessage`.
- Используется:
  - в `/support-message`;
  - в `notify_worker` для пакетной отправки изменений из `audit_events`.
- Настройки: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

### Google Sheets

- Экспорт реализован отдельным скриптом `tool_export_provider_leads.py`.
- Используются `google.oauth2.service_account` и `googleapiclient`.
- Источник данных: таблица `provider_leads` за `LEADS_EXPORT_LOOKBACK_DAYS`.
- Дедупликация по уже существующим `vid` в листе (чтение диапазона `B2:B`).
- Настройки: `GOOGLE_CREDENTIALS_FILE`, `GOOGLE_SHEET_ID`, `GOOGLE_SHEET_NAME`.

## 4) Факты по текущему состоянию задачи привязки существующих проектов

- Создание клиентов вручную уже реализовано:
  - API `POST /admin/clients`;
  - UI `AdminCreateClientModal` -> `createAdminClient(...)`.
- Отдельный API для смены владельца существующего проекта (перепривязки `projects.user_id` к другому клиенту) в текущем `backend/app/main.py` отсутствует.
- В текущем `crud.py` нет функции, которая выполняет массовую или одиночную смену владельца проекта.
- `admin_update_project` меняет параметры проекта (name/tag/status/delivery/dataLimit/regions/sites/phones/days), но не меняет `projects.user_id`.
- Текущий frontend API-клиент (`my-app-vite/src/api.ts`) не содержит метода для перепривязки проекта к другому клиенту.
- В `AdminClientProjects.tsx` редактирование проекта ограничено проверкой `project.user.id === adminUserId`; при несоответствии открывается режим `readOnly`.
- В проекте есть имперсонация клиента админом: `POST /admin/clients/{client_id}/impersonate`; токен содержит `impersonator_user_id`.
- При создании проектов через обычный клиентский поток всегда выполняется внешний вызов Prostats перед локальной записью в БД.
