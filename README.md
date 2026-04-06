# MB_LK_leads

Личный кабинет для работы с проектами и лидами:
- backend на FastAPI (`backend/app/main.py`)
- frontend на React + Vite (`my-app-vite`)
- интеграции: Prostats, Telegram, Google Sheets

В продукте сейчас три роли:
- `client` — обычный клиентский ЛК
- `agent` — manager-уровень только для своих клиентов
- `admin` — полный доступ ко всем клиентам и агентам

## Быстрые ссылки

- Архитектура: `ARCHITECTURE.md`
- Настройка PostgreSQL: `docs/PostgreSQL.md`
- Тесты Playwright: `docs/test_playwright.md`
- HTTPS и домен: `docs/setup_https_leadrecordwh.md`
- Пример проверки вебхука: `docs/webhook_test.md`

## Быстрый старт (локально)

### 1) Требования

- Node.js 24.x (рекомендовано)
- npm 11+
- Python 3.10+
- PostgreSQL (рекомендовано) или SQLite (fallback)

### 2) Установка

```bash
git clone <repository-url>
cd MB_LK_leads
```

```bash
# Python
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/macOS
source venv/bin/activate

pip install -r requirements.txt
```

```bash
# Frontend
cd my-app-vite
npm install
cd ..
```

### 3) Настройка `.env`

Файл `.env` читается backend-ом и фронтендом (для `VITE_*`, см. `my-app-vite/vite.config.ts`).

Минимально для запуска backend:

```env
WEBHOOK_SECRET=change-me
AUTH_SECRET=change-me
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/mb_lk_leads
CORS_ORIGINS=http://localhost:5173
USER_1_LOGIN=admin
USER_1_PASSWORD=admin123
```

Важно: если `WEBHOOK_SECRET` не задан, backend не стартует.

### 4) Запуск

```bash
# backend (из корня)
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
# frontend (в новом терминале)
cd my-app-vite
npm run dev
```

Открыть:
- Frontend: `http://localhost:5173`
- API: `http://localhost:8000`
- Health: `http://localhost:8000/health`

## Команды

### Frontend (`my-app-vite`)

```bash
npm run dev      # локальная разработка
npm run build    # production-сборка
npm run preview  # локальный preview production-сборки
npm run lint     # eslint
npm run test:e2e # Playwright E2E
```

### Backend (корень проекта)

```bash
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

## Переменные окружения

### Backend

| Переменная | Обязательность | Назначение |
|---|---|---|
| `WEBHOOK_SECRET` | обязательно | Секрет для `POST /api/provider-test/{secret}`; без него backend не стартует |
| `DATABASE_URL` | желательно | URL БД (`postgresql+psycopg://...`), по умолчанию `sqlite:///./app.db` |
| `AUTH_SECRET` | обязательно для прод | Секрет подписи JWT |
| `CORS_ORIGINS` | желательно | Разрешённые origin через запятую |
| `USER_1_LOGIN`, `USER_1_PASSWORD`, ... | опционально | Первичный seed пользователей при пустой БД |
| `DEBOUNCE_WINDOW_MINUTES` | опционально | Окно debounce для уведомлений |
| `SHEETS_TZ` | опционально | Таймзона отчётов/фильтров (по умолчанию `Europe/Moscow`) |
| `EXPORT_MAX_ROWS` | опционально | Лимит строк при `/leads/export` |
| `PROSTATS_TOKEN` | обязательно для CRUD проектов | Токен API Prostats |
| `PROSTATS_API_URL` | опционально | URL API Prostats (есть дефолт) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | обязательно для Telegram-функций | Уведомления и `/support-message` |
| `GOOGLE_CREDENTIALS_FILE`, `GOOGLE_SHEET_ID`, `GOOGLE_SHEET_NAME` | обязательно для экспорта в Sheets | Используются `tool_export_provider_leads.py` |
| `LEADS_EXPORT_LOOKBACK_DAYS` | опционально | Глубина выгрузки в днях (для `tool_export_provider_leads.py`) |

### Frontend (`VITE_*`)

| Переменная | Обязательность | Назначение |
|---|---|---|
| `VITE_API_BASE` | опционально | Базовый URL API (например, `/api`) |
| `VITE_REPORT_ERRORS` | опционально | Принудительная отправка ошибок с фронта в dev |
| `VITE_CLIENT_PORTAL_URL` | опционально | Базовый URL клиентского портала в админских карточках |

## API (краткая карта)

### Публичные/клиентские

- `GET /health`
- `POST /client-errors`
- `POST /api/provider-test/{secret}`
- `POST /login`
- `POST /auth/logout`
- `GET /me`
- `GET /projects`
- `POST /projects`
- `GET /projects/{project_id}`
- `PATCH /projects/{project_id}`
- `DELETE /projects/{project_id}`
- `GET /projects/{project_id}/history`
- `POST /support-message`
- `GET /leads`
- `GET /leads/export`
- `GET /reports`
- `POST /reports`
- `GET /activity/events`
- `GET /blacklist`
- `POST /blacklist`
- `DELETE /blacklist/{row_id}`
- `GET /balance`
- `GET /balance/ops`

### Manager / Admin (`/admin/*`)

Часть `/admin/*` теперь доступна не только админу, но и агенту как manager-уровню.  
Агент работает только в рамках своих клиентов.  
Отдельные эндпоинты управления агентами и имперсонация клиента остаются только для админа.

- `POST /admin/clients`
- `POST /admin/clients/{client_id}/impersonate`
- `PATCH /admin/clients/{client_id}`
- `POST /admin/clients/{client_id}/owner`
- `GET /admin/users`
- `GET /admin/agents`
- `POST /admin/agents`
- `PATCH /admin/agents/{agent_id}`
- `GET /admin/agents/{agent_id}/balance`
- `GET /admin/agents/{agent_id}/balance/ops`
- `POST /admin/agents/{agent_id}/balance/ops`
- `GET /admin/projects`
- `GET /admin/projects/{project_id}`
- `GET /admin/projects/{project_id}/history`
- `PATCH /admin/projects/{project_id}`
- `DELETE /admin/projects/{project_id}`
- `GET /admin/leads`
- `GET /admin/blacklist`
- `GET /admin/reports`
- `POST /admin/reports`
- `GET /admin/changes/summary`
- `GET /admin/changes/{client_id}`
- `POST /admin/changes/{event_id}/resolve`
- `GET /admin/clients/summary`
- `GET /admin/clients/{client_id}/collection-state`
- `POST /admin/clients/{client_id}/collection/pause`
- `POST /admin/clients/{client_id}/collection/resume`
- `GET /admin/clients/{client_id}/balance`
- `GET /admin/clients/{client_id}/balance/ops`
- `POST /admin/clients/{client_id}/balance/ops`
- `GET /admin/clients/{client_id}/tariffs`
- `POST /admin/clients/{client_id}/tariffs`
- `GET /admin/tariffs/{tariff_id}/ops`
- `POST /admin/tariffs/{tariff_id}/ops`

Ключевые ограничения manager-уровня:
- агент видит и редактирует только своих клиентов;
- агент может только начислять старый баланс своим клиентам;
- агент не может списывать старый баланс своим клиентам;
- агент может управлять тарифами только своих клиентов;
- админ не может пополнять старый баланс клиента, закреплённого за агентом.

## Особенности работы с проектами

- Один ввод названия проекта в клиентском ЛК может создать несколько проектов: по одному на каждый выбранный источник данных `B1` / `B2` / `B3` / `B4`.
- Для части клиентов в карточке доступна опция **уникальных имён новых проектов**.
- Если опция включена, новые проекты создаются в формате:
  - `B1_[MB54] Магнум`
  - `B2_[MB55] Магнум`
- Для таких клиентов создание проекта двухшаговое:
  - сначала create у Prostats;
  - затем локальная запись в БД;
  - затем rename у Prostats в финальное имя с `[MB{id}]`.
- Технические префиксы имени проекта нельзя удалять при редактировании:
  - всегда обязателен `B1_` / `B2_` / `B3_` / `B4_`;
  - если проект создан по схеме с уникальным именем, обязателен и маркер `[MB{id}]`.

## Агентский уровень

- Клиент может быть либо прямым клиентом админа, либо клиентом конкретного агента.
- При переводе клиента агенту его текущий старый остаток становится долгом агента.
- Агент может уходить в минус.
- Минус агента блокирует только новые начисления его клиентам, но не ставит проекты на паузу автоматически.
- При отключении агента:
  - агент не может войти;
  - проекты его клиентов ставятся на паузу;
  - клиентам агента блокируется редактирование проектов.
- При повторном включении агента:
  - вход снова работает;
  - блокировка редактирования проектов у его клиентов снимается;
  - сами проекты автоматически не переводятся в `Активен`.

## Вебхук провайдера и экспорт в Google Sheets

### Вебхук

- Endpoint: `POST /api/provider-test/{WEBHOOK_SECRET}`
- Лог запроса: `logs/provider_webhook.log`
- Дедупликация по `vid`
- Данные сохраняются в таблицу `provider_leads`
- Привязка к проекту идёт по имени `payload.page`, но только среди **неудалённых** проектов
- Если найден один проект — лид привязывается к нему
- Если совпадений нет — лид сохраняется без `project_id`
- Если найдено несколько проектов с одинаковым именем — backend не падает: лид сохраняется без `project_id`, а в Telegram уходит техническое уведомление в общий чат

Для ручной проверки есть отдельный сервер-скрипт `webhook_test.py`.

### Экспорт provider leads

Скрипт: `tool_export_provider_leads.py`

```bash
python tool_export_provider_leads.py
```

Лог экспорта: `logs/provider_export.log`.

## Полезные утилиты

- `tool_inspect_db.py` — просмотр `projects` и выборок `provider_leads`
- `util_01_gck_projects_dump.py` — дамп проектов Prostats
- `util_02_gck_project_dump.py` — дамп одного проекта Prostats
- `util_03_gck_project_create_and_dump.py` — создать проект и сразу получить дамп
- `util_04_projects_compare_dump.py` — сравнение проектов
- `util_05_gck_project_delete.py` — удаление проекта в Prostats
- `util_06_prostats_sync_check.py` — проверка синхронизации с Prostats
- `util_07_gck_project_update.py` — обновление проекта в Prostats
- `util_table_explorer.py` — обзор таблиц БД

## Структура проекта

```text
MB_LK_leads/
├── backend/                 # FastAPI backend
│   └── app/
│       ├── main.py          # API endpoints и запуск приложения
│       ├── models.py        # SQLAlchemy модели
│       ├── crud.py          # Операции с БД
│       ├── schemas.py       # Pydantic схемы
│       ├── auth.py          # JWT/bcrypt
│       ├── providers/       # Интеграция с Prostats
│       └── ...
├── my-app-vite/             # React + Vite frontend
│   ├── src/
│   ├── e2e/                 # Playwright тесты
│   └── package.json
├── docs/                    # Доп. документация
├── tool_*.py                # Утилиты проекта
├── util_*.py                # Утилиты интеграции/диагностики
├── ARCHITECTURE.md
├── requirements.txt
└── README.md
```

## Тестирование

- Frontend lint: `cd my-app-vite && npm run lint`
- E2E: `cd my-app-vite && npm run test:e2e`
- Проверка backend: `GET /health`

Подробности по e2e: `docs/test_playwright.md`.

## Деплой

См. отдельные инструкции:
- `docs/setup-https-leadrecordwh.md`
- `docs/SWAP-SETUP.md`
- `docs/git-ssh-setup.md`
- `docs/deployment-guide.md`

## Примечания

- Админ определяется как пользователь с `id=1` (логика в `backend/app/main.py`).
- Для роли `agent` доступ ограничивается server-side, а не только UI.
- Таблицы создаются автоматически через `models.Base.metadata.create_all(...)` при старте.
- `TELEGRAM_CHAT_ID` используется не только для обычных уведомлений, но и для технических alert-ов:
  - неоднозначная привязка лида к проекту по имени;
  - сбой финализации уникального имени проекта.
- В репозиторий не коммитятся `.env`, `credentials/`, `logs/`, `*.db` (см. `.gitignore`).
