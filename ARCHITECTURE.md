# ARCHITECTURE

Короткий контекст проекта для старта нового чата с ИИ.
Цель: быстро дать модели рабочую карту проекта без перегруза деталями.

Last updated: 2026-02-19

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
5. Фильтры дат и отчёты завязаны на `SHEETS_TZ` (по умолчанию `Europe/Moscow`).

## 4) Key Domain Objects

- `User` / `ClientProfile`
- `Project`
- `ProviderLead`
- `AuditEvent`
- `ClientBalanceOperation`
- `ClientProjectPauseSnapshot`

Смотри `backend/app/models.py`.

## 5) Runtime Flows

### A) UI -> API
Frontend вызывает API -> backend проверяет auth/roles -> `crud.py` -> БД/интеграции -> ответ в UI.

### B) Provider Webhook
Провайдер вызывает `POST /api/provider-test/{secret}` -> валидация секрета/payload -> запись в `provider_leads` (или skip дубля по `vid`) -> лог в `logs/provider_webhook.log`.

### C) Notifications Worker
На старте backend запускает `notify_worker` -> воркер агрегирует pending-события -> отправляет в Telegram -> помечает обработанные.

### D) Admin Operations
`/admin/*` -> проверка админ-доступа -> `crud.py` (клиенты/проекты/баланс/аудит) -> при необходимости синхронизация статусов с Prostats.

## 6) Where To Change Code By Task Type

- Новое поле/правило в API: `schemas.py` + `crud.py` + endpoint в `main.py`
- UI + API контракт: `my-app-vite/src/api.ts` + backend endpoint/schema
- Интеграция Prostats: `backend/app/providers/prostats.py`
- Telegram-уведомления: `backend/app/telegram.py`, `backend/app/notify_worker.py`
- Экспорт provider leads: `tool_export_provider_leads.py`
- Проблемы времени/дат: `backend/app/time_utils.py` и места фильтрации в `main.py`

## 7) Env Groups (Quick)

База и auth:
- `DATABASE_URL`
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
- `SHEETS_TZ`
- `DEBOUNCE_WINDOW_MINUTES`
- `EXPORT_MAX_ROWS`

## 8) Known Pitfalls

1. Не полагаться только на frontend-проверки прав.
2. Не менять бизнес-правила только в UI, без `crud.py`.
3. При изменениях endpoint проверять `my-app-vite/src/api.ts` на совместимость.
4. Не ломать webhook-дедупликацию по `vid`.
5. Учитывать, что часть логики сосредоточена в `main.py` и `crud.py`.

