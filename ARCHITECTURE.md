# ARCHITECTURE

Короткий контекст проекта для старта нового чата с ИИ.
Цель: быстро дать модели рабочую карту проекта без перегруза деталями.

Last updated: 2026-03-23

## 1) System At A Glance

`MB_LK_leads` — личный кабинет для работы с проектами и лидами.

- Backend: FastAPI (`backend/app/main.py`)
- Frontend: React + Vite (`my-app-vite/src`)
- БД: PostgreSQL (основной контур), SQLite (fallback локально)
- Интеграции: Prostats, Telegram, Google Sheets

## 2) Source Of Truth

Если есть конфликт между документами и кодом, доверять коду:

- API endpoints: `backend/app/main.py`
- Бизнес-логика: `backend/app/crud.py`
- Модели БД: `backend/app/models.py`
- Схемы API: `backend/app/schemas.py`
- Front API client: `my-app-vite/src/api.ts`
- Запуск/команды: `README.md`

## 3) Critical Invariants

1. Админ определяется как `user.id == 1`.
2. `WEBHOOK_SECRET` обязателен: без него backend не стартует.
3. Операции с проектами должны синхронизироваться с Prostats.
4. Вебхук провайдера дедуплицирует `provider_leads` по `vid`.
5. Вебхук матчит лиды по имени проекта только среди неудалённых проектов; при `0` или `>1` совпадениях лид сохраняется без `project_id`, без падения backend.
6. Для части клиентов новые проекты могут создаваться с обязательным маркером `[MB{id}]` в имени; если маркер применён, его нельзя удалять при редактировании.
7. Технический префикс источника `B1_` / `B2_` / `B3_` / `B4_` обязателен в названии проекта.
8. Фильтры дат и отчёты завязаны на `SHEETS_TZ` (по умолчанию `Europe/Moscow`).
9. Вебхук провайдера не запускает автоконтроль лимитов по событию: лимит-контроль работает только периодическим фоновым циклом.

## 4) Key Domain Objects

- `User` / `ClientProfile`
- `Project`
- `ProviderLead`
- `AuditEvent`
- `ClientBalanceOperation`
- `ClientProjectPauseSnapshot`

Важно:
- `User` содержит пер-клиентные флаги, в т.ч. `auto_limit_control_enabled`, Telegram-настройки и `unique_project_names_enabled`.
- `Project` содержит `provider_project_id` и флаг `unique_name_applied` для проектов, где имя зафиксировано в формате с `[MB{id}]`.

Смотри `backend/app/models.py`.

## 5) Runtime Flows

### A) UI -> API
Frontend вызывает API -> backend проверяет auth/roles -> `crud.py` -> БД/интеграции -> ответ в UI.

### B) Provider Webhook
Провайдер вызывает `POST /api/provider-test/{secret}` -> валидация секрета/payload -> поиск проекта по `payload.page` только среди неудалённых проектов -> запись в `provider_leads` (или skip дубля по `vid`) -> лог в `logs/provider_webhook.log` -> быстрый ответ без запуска лимит-контроля в этом же запросе.

Матчинг по имени:
- `0` совпадений -> лид сохраняется без `project_id`;
- `1` совпадение -> лид привязывается к проекту;
- `>1` совпадений -> лид сохраняется без `project_id`, в общий Telegram уходит alert о неоднозначной привязке.

### C) Notifications Worker
На старте backend запускает `notify_worker` -> воркер закрывает debounce-очередь по `audit_events` и помечает события как обработанные для отправки.
Важно: с 2026-03-10 батч-уведомления по изменениям проектов и чёрного списка в Telegram отключены.
В Telegram остаются только отдельные сообщения из логики автопаузы по лимитам и из формы поддержки.

### D) Periodic Limit Control
На старте backend запускает отдельный поток лимит-контроля -> раз в `AUTO_LIMIT_CHECK_SECONDS` секунд после завершения предыдущего прохода выбираются клиенты с `auto_limit_control_enabled=true` -> для каждого клиента пересчитывается остаток и сумма лимитов активных проектов -> при превышении лимитов проекты ставятся на паузу через Prostats и локальную БД -> при необходимости отправляются точечные Telegram-уведомления.

Текущая модель специально упрощена:
- webhook не ставит задачи лимит-контроля;
- лимит-контроль работает только по интервалу;
- фактический период цикла = полный проход по клиентам + `sleep(interval)`.

### E) Admin Operations
`/admin/*` -> проверка админ-доступа -> `crud.py` (клиенты/проекты/баланс/аудит) -> при необходимости синхронизация статусов с Prostats.

### F) Project Create With Unique Name
Для клиентов с `unique_project_names_enabled=true` создание проекта двухшаговое:
UI отправляет обычное имя вида `B1_Магнум` -> backend создаёт проект у Prostats -> создаёт локальный `Project` и получает `project.id` -> backend делает rename у Prostats в формат `B1_[MB54] Магнум` -> сохраняет финальное имя у нас.

Если rename у Prostats не подтверждён:
- backend делает retry с коротким backoff;
- пользователь получает понятный warning;
- в общий Telegram уходит техническое уведомление.

## 6) Where To Change Code By Task Type

- Новое поле/правило в API: `schemas.py` + `crud.py` + endpoint в `main.py`
- UI + API контракт: `my-app-vite/src/api.ts` + backend endpoint/schema
- Интеграция Prostats: `backend/app/providers/prostats.py`
- Логика уникальных имён проектов и server-side валидация имени: `backend/app/main.py` + `backend/app/crud.py`
- Telegram-отправка: `backend/app/telegram.py`
- Debounce-воркер по `audit_events`: `backend/app/notify_worker.py`
- Экспорт provider leads: `tool_export_provider_leads.py`
- Проблемы времени/дат: `backend/app/time_utils.py` и места фильтрации в `main.py`

## 7) Env Groups (Quick)

База и auth:
- `DATABASE_URL`
- `DB_POOL_SIZE`
- `DB_MAX_OVERFLOW`
- `DB_POOL_TIMEOUT`
- `DB_POOL_RECYCLE`
- `AUTH_SECRET`
- `CORS_ORIGINS`

Webhook и провайдер:
- `WEBHOOK_SECRET`
- `PROSTATS_TOKEN`
- `PROSTATS_API_URL`

Telegram:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Google Sheets export:
- `GOOGLE_CREDENTIALS_FILE`
- `GOOGLE_SHEET_ID`
- `GOOGLE_SHEET_NAME`
- `LEADS_EXPORT_LOOKBACK_DAYS`

Дополнительно:
- `AUTO_LIMIT_CHECK_SECONDS`
- `SHEETS_TZ`
- `DEBOUNCE_WINDOW_MINUTES`
- `EXPORT_MAX_ROWS`

## 8) Known Pitfalls

1. Не полагаться только на frontend-проверки прав.
2. Не менять бизнес-правила только в UI, без `crud.py`.
3. При изменениях endpoint проверять `my-app-vite/src/api.ts` на совместимость.
4. Не ломать webhook-дедупликацию по `vid`.
5. Не убирать server-side защиту технических префиксов имени проекта: `B1_...B4_` и `[MB{id}]` для `unique_name_applied`.
6. Для клиентов с уникальными именами создание проекта не атомарно в одном внешнем вызове: после create нужен rename в Prostats с retry-логикой.
7. Учитывать, что часть логики сосредоточена в `main.py` и `crud.py`.
