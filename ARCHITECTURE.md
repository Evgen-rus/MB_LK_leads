# Архитектура MB_LK_leads

## Назначение и границы

`MB_LK_leads` — личный кабинет для управления клиентами, проектами и лидами. Это единое приложение с React/Vite frontend и FastAPI backend, а не набор независимых сервисов. Backend хранит операционные данные, управляет проектами у поставщика Prostats, принимает provider/Pixel webhooks и ставит Telegram-уведомления в outbox. Отдельный `telegram_worker.py` доставляет outbox в Telegram через HTTPS API backend.

Подтверждённые контуры:

- React 19 + TypeScript + Vite: `my-app-vite/src/`.
- FastAPI + SQLAlchemy: `backend/app/`.
- Хранилище: `DATABASE_URL`; код поддерживает PostgreSQL и локальный SQLite fallback.
- Внешние границы: Prostats, Telegram Bot API через внешний worker, Google Sheets через служебные export-скрипты.

В репозитории нет Docker/Compose, CI-конфигурации, отдельного backend test runner и формального мигратора. Не предполагайте их наличие.

## Карта исходников и источники истины

| Что меняется | Источник истины | Что ещё проверить |
| --- | --- | --- |
| HTTP API, зависимости и фоновые циклы | `backend/app/main.py` | `schemas.py`, `crud.py`, frontend `src/api.ts` |
| Данные, запросы и предметные правила | `backend/app/models.py`, `backend/app/crud.py` | startup schema-evolution в `main.py` |
| Вход и права | `backend/app/auth.py`, dependencies в `main.py` | все затронутые admin/agent/client endpoints |
| Проекты у Prostats | `backend/app/providers/prostats.py` | create/update/delete flows и local audit/operation events |
| Долговечные массовые операции проектов | `models.py` (`ProjectOperationJob`/`ProjectOperationItem`), `crud.py`, `project_operations_worker.py` | endpoints в `main.py`, Prostats adapter и frontend polling |
| Provider/Pixel XLSX import | `backend/app/provider_leads_xlsx_import.py` | preview/commit endpoints и `provider_leads` |
| Telegram outbox | `notifications.py`, `notify_worker.py`, `telegram_worker.py` | internal claim/result API и `telegram_notifications` |
| Экран и запросы frontend | `my-app-vite/src/App.tsx`, `my-app-vite/src/components/`, `my-app-vite/src/api.ts` | API responses, роли и lazy loading manager screens |

`README.md` — только быстрый локальный запуск. `docs/` — runbook и продуктовый контекст; если они расходятся с кодом или конфигурацией, приоритет у кода.

## Доменные объекты и владение

- `User` и `ClientProfile`: пользователи, роли `admin`/`agent`/`client`, привязка клиента к агенту, настройки доступа и уведомлений.
- `Project`: локальная проекция проекта; хранит владельца, идентификатор у поставщика, источник, гео/контент, статусы и лимиты. Provider-проект связан с Prostats, Pixel-проект — исключение с доменным именем и `UNMAPPED` источником.
- `ProviderLead`: provider- или pixel-лид, привязанный к проекту, либо сохранённый непривязанным в provider-flow при проблемном матчинге.
- `ClientTariff` и `ClientTariffOperation`: тарифные остатки и пороги уведомлений; `ClientBalanceOperation` — отдельный legacy-балансный контур.
- `AuditEvent` хранит успешные проектные операции, а `ProjectOperationEvent` — неуспешные; не смешивайте их.
- `ProjectOperationJob` и `ProjectOperationItem` хранят выполняемые массовые provider-операции и их прогресс. Это не журнал аудита: незавершённые записи claim-ит фоновый worker, а временные ошибки переводятся в `waiting_retry`.
- `TelegramNotification` — долговечный outbox; `PixelTelegramReportState` отвечает за дедупликацию отчётов Pixel.

## Основные runtime-потоки

```mermaid
flowchart LR
  UI["React/Vite UI"] --> API["FastAPI main.py"]
  API --> DB[("PostgreSQL or SQLite")]
  API <--> PRO["Prostats API"]
  Provider["Provider webhook"] --> API
  Pixel["Pixel webhook"] --> API
  API --> OUT["telegram_notifications outbox"]
  TW["telegram_worker.py"] <--> API
  TW --> TG["Telegram Bot API"]
```

### UI и API

`src/main.tsx` запускает клиентское логирование и обработчик impersonation, затем рендерит `App.tsx`. `App.tsx` выбирает экран по локальному состоянию и lazy-loads тяжёлые admin/manager экраны. Браузер использует JWT; backend остаётся единственной границей авторизации.

### Проекты и Prostats

Создание, редактирование и удаление provider-проектов выполняются через `main.py` и `providers/prostats.py`, с локальной записью состояния и истории. UI display-коды `A`–`D` соответствуют raw-кодам `B1`–`B4`; raw-коды остаются контрактом API, БД, XLSX и Prostats. Pixel не должен получать этот mapping.

Критические правила:

- admin-only проверяется через `user.id == 1`; хранение роли `admin` само по себе не заменяет эту проверку.
- agent ограничен своими клиентами через `users.owner_agent_id`; UI-ограничения не являются защитой.
- не держите request-scoped DB session во время внешнего вызова Prostats или Telegram: используйте короткие read/write-сессии вокруг вызова.
- не превращайте неуспешные операции у поставщика в успешные `audit_events`.
- Массовые изменения provider-проектов, включая общую паузу/возобновление клиента, сначала фиксируются в БД и выполняются process-local worker-ом. Закрытие браузера не прерывает job; после перезапуска backend протухшая lease восстанавливается и job продолжается.
- Для одного клиента разрешена одна активная provider-job. Pixel-проекты в этот контур не входят. Клиент и агент получают только нейтральное сообщение «Сервис обработки данных»; техническая причина и название Prostats доступны только администратору и в безопасных логах.
- При общей паузе бизнес-блокировка проектов устанавливается до начала внешних вызовов и остаётся после завершения паузы. При возобновлении она снимается только когда snapshot восстановленных проектов опустел.

### Входящие лиды

`POST /api/provider-test/{secret}` принимает provider-payload. Он сопоставляет имя проекта сначала с неудалёнными проектами, затем с удалёнными в 48-часовом grace-окне. Неоднозначный или отсутствующий матч не должен ронять backend: provider-лид может сохраниться без `project_id`.

`POST /api/pixel-webhook/{secret}` — отдельный поток: сопоставление идёт по домену `site`; при `domain_not_found` или `domain_ambiguous` строка не создаётся. Дедупликация также различается: provider — `lead_source="provider"` и `vid`; Pixel — `lead_source="pixel"`, `vid` и телефон. Операционная дата для кабинета, отчётов и дашбордов — `imported_at`, не `prov_created_at`.

### XLSX preview/commit

Админские import endpoints используют общий модуль `provider_leads_xlsx_import.py`. Preview сохраняет временные файлы и state в локальной temp-директории процесса; commit разрешён только тому же админу и только с валидным `previewId`. Неоднозначная или отсутствующая привязка проекта блокирует commit. Следствие: без sticky session preview и commit на разных инстансах не работают.

### Уведомления и периодические проверки

Backend кладёт сообщения в `telegram_notifications`; Bot API вызывает только отдельный `telegram_worker.py`, который claim-ит сообщения и сообщает результат через защищённые internal endpoints. Startup в `main.py` запускает daemon threads для outbox, долговечных project jobs, тарифных/лимитных проверок, Pixel-отчётов, очистки outbox и проверки B4 operator block. Project worker использует DB lease и допускает восстановление после падения процесса; остальные расписания остаются process-local. Эти потоки создаются в каждом процессе backend; масштабирование несколькими процессами требует проверки дедупликации и расписаний.

Дневной лимит проекта и остаток клиентского тарифа — независимые контуры. Не заменяйте один другим; проверка лимитов не запускается непосредственно на provider webhook.

## Устойчивые инварианты

- `WEBHOOK_SECRET` обязателен для старта backend; секрет Pixel отдельный.
- Серверные проверки preview/commit, прав, дедупликации и технических префиксов provider-проектов нельзя переносить только на frontend.
- Новые provider-проекты используют raw-префикс `B1_`…`B4_`; для клиентов с unique-name настройкой сохранённый internal prefix применяется только к новым проектам. Legacy-маркер `[MB{id}]` не удаляйте без отдельной миграции.
- `Блокировка оператора` — системный статус только для активных B4 при подтверждённом `status == 0` или пустом успешном ответе Prostats; не выставляйте его вручную и не распространяйте на B1–B3.
- `Архив` — ручной статус. Если проект был `Активен`, сначала выключается у поставщика; локальный `Архив` ставится только после успеха. В списках скрыт, пока не включён `includeArchived`. В агрегатах считается как `На паузе`. Удалённые проекты архивировать нельзя.
- Агрегаты dashboard и client summary рассчитываются на backend. Не пересчитывайте их из больших raw-списков в браузере.
- Полные history snapshots могут содержать чувствительные поля: list endpoints не должны возвращать расширенные `before`/`after` или `duplicateDiagnostics` клиентам и агентам.

## Карта влияния изменений

| Изменение | Затрагиваемые контуры |
| --- | --- |
| API payload/response | `schemas.py` → `main.py` → `src/api.ts` → компоненты и E2E, если сценарий покрыт |
| Модель/поле БД | `models.py` → `crud.py` → schema-evolution в `main.py` → API consumers |
| Права или impersonation | backend dependencies и manager-access helpers → все role-specific endpoints → frontend доступность |
| Provider project flow | validation → Prostats adapter → local persistence/audit → UI error handling |
| Массовая provider-операция | job/items schema → claim/retry worker → Prostats reconciliation → local persistence/audit → polling UI и role-aware сообщения |
| Provider/Pixel lead logic | webhook или import module → `provider_leads` → reports/dashboard/export/notification consumers |
| Telegram notification | creator → outbox CRUD → internal worker API → `telegram_worker.py` |
| Тяжёлый admin screen | lazy import в `App.tsx` → component → `src/api.ts`; не возвращайте статический runtime-import |

## Проверка и известные пробелы

Поддерживаемые frontend-проверки в `my-app-vite/package.json`: `npm run lint` и `npm run build`. Backend test runner, CI и migration tool не обнаружены.

Архитектурные риски текущей реализации, а не обещания системы:

- Schema changes исполняются на import/startup через `create_all` и набор `ALTER TABLE`, поэтому их нужно проверять на целевой SQL dialect и на уже существующей БД.
- Временные XLSX previews не разделяются между инстансами.
- Фоновые daemon threads являются process-local; при нескольких backend workers их расписания повторяются.
- Project jobs сохраняются при остановке backend и продолжатся после запуска, но не выполняются, пока все backend-процессы выключены. Для непрерывной работы во время web-deploy worker потребуется вынести в отдельный процесс.
- Большая часть маршрутизации и бизнес-координации сосредоточена в `main.py` и `crud.py`; при изменении одного из них ищите связанных consumers, а не ограничивайтесь одной функцией.

Эта карта содержит только устойчивые связи. Детали единичных багов, конкретных инцидентов и пошаговые операции должны оставаться в `docs/`, а не добавляться сюда.
