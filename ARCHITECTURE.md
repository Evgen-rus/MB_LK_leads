# ARCHITECTURE

Короткий контекст проекта для старта нового чата с ИИ.
Цель: быстро дать модели рабочую карту проекта без перегруза деталями.

Last updated: 2026-04-30

## 1) System At A Glance

`MB_LK_leads` — личный кабинет для работы с проектами и лидами.

- Backend: FastAPI (`backend/app/main.py`)
- Frontend: React + Vite (`my-app-vite/src`)
- БД: PostgreSQL (основной контур), SQLite (fallback локально)
- Интеграции: Prostats, Telegram, Google Sheets
- **Импорт лидов провайдера из XLSX:** админский UI (двухшаговый preview → commit) и служебный CLI; общая логика в `backend/app/provider_leads_xlsx_import.py`; для upload-эндпоинтов нужен **`python-multipart`**

## 2) Source Of Truth

Если есть конфликт между документами и кодом, доверять коду:

- API endpoints: `backend/app/main.py`
- Импорт лидов из XLSX (preview/commit, парсинг, валидация): `backend/app/provider_leads_xlsx_import.py`
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
5. Вебхук матчит лиды по имени проекта в два шага: сначала только среди неудалённых проектов; если совпадений нет, делает fallback к удалённым проектам с неистёкшим grace-окном `48h` (`provider_leads_grace_until`). При `0` или `>1` совпадениях лид сохраняется без `project_id`, без падения backend.
6. Для части клиентов новые проекты могут создаваться с обязательным маркером `[MB{id}]` в имени; если маркер применён, его нельзя удалять при редактировании.
7. Технический префикс источника `B1_` / `B2_` / `B3_` / `B4_` обязателен в названии проекта.
8. Во frontend действует display-mapping кодов источника: пользователю показываются `A` / `B` / `C` / `D`, но source-of-truth в API, БД, webhook, XLSX и Prostats остаётся `B1` / `B2` / `B3` / `B4`; для имён проектов UI тоже показывает display-вид, а перед submit отправляет raw-вид.
9. Фильтры дат и отчёты завязаны на `SHEETS_TZ` (по умолчанию `Europe/Moscow`).
10. Вебхук провайдера не запускает автоконтроль лимитов по событию: лимит-контроль работает только периодическим фоновым циклом.
11. **Админский импорт лидов из XLSX:** запись в БД только через **commit** по существующему `previewId`; сессия preview привязана к **тому же** админу, что и commit; при строках без однозначного проекта или с **неоднозначным** матчингом проекта commit **запрещён**; при записи учитываются дубли по **`vid`** (как у вебхука). Парсинг `prov_chanel` / `prov_source` и привязка к проекту согласованы с вебхук-потоком.
12. Роли в системе теперь три: `admin`, `agent`, `client`. Админ по-прежнему определяется как `user.id == 1`, но server-side доступ дальше ограничивается ещё и ролью.
13. Клиент может быть либо прямым клиентом админа, либо клиентом агента через `users.owner_agent_id`.
14. Агентский баланс больше не является отдельным учётным контуром. В manager-зоне он считается как сумма текущих `remaining` всех клиентов, закреплённых за агентом через `users.owner_agent_id`.
15. Агент может работать только в рамках своих клиентов: управлять проектами, лидами, отчётами, чёрным списком и смотреть тарифы своих клиентов в режиме read-only. Изменение баланса и тарифов клиентов выполняет только админ.
16. При отключении агента его вход блокируется, проекты его клиентов ставятся на паузу, а редактирование проектов у этих клиентов блокируется. При повторном включении агента блокировка редактирования снимается, но проекты автоматически не переводятся в `Активен`.
17. У каждого клиентского тарифа теперь есть три обязательных Telegram-сигнала остатка: `signal1 > signal2 > signal3 > 0`, при этом `signal1 < текущий размер тарифа`. Сигналы задаются и при создании тарифа, и при его редактировании.

## 4) Key Domain Objects

- `User` / `ClientProfile`
- `Project`
- `ProviderLead`
- `AuditEvent`
- `ProjectOperationEvent`
- `ClientBalanceOperation`
- `ClientTariff`
- `ClientTariffOperation`
- `ClientProjectPauseSnapshot`

Важно:
- `User` теперь хранит не только данные авторизации, но и роль/иерархию доступа: `display_name`, `role`, `owner_agent_id`, `is_disabled`, а также пер-клиентные флаги, в т.ч. `auto_limit_control_enabled`, Telegram-настройки и `unique_project_names_enabled`. Для Telegram-сигналов тарифа у клиента также хранится последний уже отправленный уровень сигнала, чтобы повторно слать alert только после восстановления остатка выше порога.
- `Project` содержит `provider_project_id`, флаг `unique_name_applied`, а также поля мягкого удаления `deleted_at` и `provider_leads_grace_until` для grace-привязки хвостовых webhook-лидов после удаления проекта.
- `AuditEvent` хранит успешные изменения проектов/ЧС и участвует в админской очереди необработанных изменений; `ProjectOperationEvent` хранит только неудачные попытки `create` / `update` / `delete` проектов и не попадает в эту очередь.
- `ClientBalanceOperation` остаётся старым контуром баланса клиента: он участвует в расчёте `remaining`, лимит-контроле и автопаузе проектов. Агентский баланс теперь не хранится отдельно, а агрегируется из текущих остатков клиентов агента.
- `ClientTariff` и `ClientTariffOperation` — тарифный контур клиента. Каждая тарифная операция зеркалится в `ClientBalanceOperation`, поэтому тарифы влияют на `remaining`, лимит-контроль и автопаузу. У тарифа есть обязательные пороги `signal1` / `signal2` / `signal3` для Telegram-уведомлений по остатку клиента. Тариф назначается только клиенту; изменять тарифы может только админ, агенту доступен только просмотр тарифов и их истории у своих клиентов.

Смотри `backend/app/models.py`.

## 5) Runtime Flows

### A) UI -> API
Frontend вызывает API -> backend проверяет auth/roles -> `crud.py` -> БД/интеграции -> ответ в UI.

История проектов/действий в UI собирается из двух источников:
- успешные изменения (`create` / `update` / `delete`) пишутся в `audit_events`;
- неудачные попытки проектных операций пишутся в `project_operation_events` со статусом `failed`, показываются с результатом `Ошибка`, но не требуют админской отметки “выполнено”.

### B) Provider Webhook
Провайдер вызывает `POST /api/provider-test/{secret}` -> валидация секрета/payload -> поиск проекта по `payload.page`: сначала среди неудалённых, затем fallback к удалённым с неистёкшим `provider_leads_grace_until` -> запись в `provider_leads` (или skip дубля по `vid`) -> лог в `logs/provider_webhook.log` -> быстрый ответ без запуска лимит-контроля в этом же запросе.

Матчинг по имени:
- `0` совпадений -> лид сохраняется без `project_id`;
- `1` совпадение -> лид привязывается к проекту;
- `>1` совпадений -> лид сохраняется без `project_id`, в общий Telegram уходит alert о неоднозначной привязке.

Важно:
- если есть новый неудалённый проект с тем же именем, он имеет приоритет над удалённым проектом в grace-окне;
- fallback к удалённому проекту возможен только если среди неудалённых совпадений нет;
- удаление проекта во фронте и удаление у провайдера происходят сразу, но локальная привязка хвостовых лидов к уже удалённому проекту сохраняется ещё `48h`.

### C) Notifications Worker
На старте backend запускает `notify_worker` -> воркер закрывает debounce-очередь по `audit_events` и помечает события как обработанные для отправки.
Важно: с 2026-03-10 батч-уведомления по изменениям проектов и чёрного списка в Telegram отключены.
В Telegram остаются только отдельные сообщения из логики автопаузы по лимитам и из формы поддержки.

### D) Periodic Limit Control
На старте backend запускает отдельный поток лимит-контроля -> раз в `AUTO_LIMIT_CHECK_SECONDS` секунд после завершения предыдущего прохода выбираются клиенты с `auto_limit_control_enabled=true` -> для каждого клиента пересчитывается остаток и сумма лимитов активных проектов -> при превышении лимитов проекты ставятся на паузу через Prostats и локальную БД -> при необходимости отправляются точечные Telegram-уведомления.

В этом же фоне теперь живёт и проверка тарифных сигналов:
- берётся текущий `remaining` клиента;
- выбирается последний созданный тариф клиента;
- если остаток впервые пересёк `signal1` / `signal2` / `signal3`, отправляется Telegram-сообщение;
- если за один проход остаток пересёк несколько порогов сразу, уходит **одно** сообщение только по самому нижнему достигнутому сигналу;
- после пополнения выше порога сигнал может сработать повторно при следующем пересечении вниз;
- ошибка отправки в Telegram не ломает цикл и не фиксирует сигнал как доставленный: будет повторная попытка на следующем проходе.

Текущая модель специально упрощена:
- webhook не ставит задачи лимит-контроля;
- лимит-контроль работает только по интервалу;
- фактический период цикла = полный проход по клиентам + `sleep(interval)`.
- поэтому Telegram-сигналы тарифа тоже не отправляются мгновенно на каждый новый лид, а проверяются только в рамках этого периодического цикла.

### E) Manager Operations
`/admin/*` больше не означает доступ только админа. В этой зоне теперь два manager-уровня:

- `admin` -> полный доступ ко всем клиентам, агентам, балансам, тарифам и переводам владения;
- `agent` -> доступ только к своим клиентам и их сущностям.

Базовая схема:

- frontend вызывает `/admin/*`;
- backend проверяет auth и manager-role;
- дальше `crud.py` дополнительно ограничивает доступ по `owner_agent_id` / доступному клиенту;
- при необходимости выполняется синхронизация статусов с Prostats.

Отдельные правила:

- прямые ручные операции старого баланса клиента из manager-зоны отключены;
- баланс клиента меняется через тарифные операции админа и через фактический расход идентификаций по проектам;
- агент не может начислять или списывать баланс клиента в ЛК;
- агент не может создавать тариф клиенту, добавлять к нему или списывать из него;
- агент может только смотреть тарифы и историю тарифов своих клиентов;
- тарифы агентов отключены.

### F) Admin Tariffs
Админский экран `Баланс` использует popup-менеджер тарифов для работы с клиентским тарифом. Прямые кнопки `Начислить номера` / `Списать номера` для клиента больше не используются.

Текущая модель тарифов:

- каждый новый тариф создаётся как отдельная запись клиента;
- при создании и редактировании тарифа админ обязан задать `signal1` / `signal2` / `signal3`;
- сигналы валидируются по правилу `signal1 > signal2 > signal3 > 0`, при этом `signal1` должен быть меньше текущего размера тарифа;
- внутри конкретного тарифа можно делать `credit` / `debit` корректировки с обязательным комментарием;
- создание тарифа и его корректировки автоматически создают зеркальную запись в `ClientBalanceOperation`;
- поэтому тарифные операции меняют баланс клиента и участвуют в расчёте `remaining`, лимит-контроле и автопаузе;
- Telegram-сигналы тарифа проверяются по `remaining`, а не по сумме лимитов активных проектов;
- в админской таблице `Клиенты` и в сводке клиента `Текущий тариф` показывается не сумма всех тарифов, а **последний созданный тариф** с учётом его внутренних корректировок;
- если тарифов у клиента нет, в UI показывается прочерк `-`;
- история старого баланса клиента сохраняется: старые ручные записи остаются как legacy, новые записи баланса появляются как следствие тарифных операций.

Для агента доступен только read-only просмотр тарифов и истории тарифов своих клиентов. Агентские тарифы и агентское управление тарифами отключены.

### F2) Agent Disable / Enable

При отключении агента:

- агент не может войти в систему;
- активные проекты его клиентов переводятся в `На паузе`;
- у клиентов агента включается `projects_mutation_locked` с причиной `Агент отключён администратором`.

При повторном включении агента:

- сам агент снова может войти;
- `projects_mutation_locked` у его клиентов снимается только если блокировка была поставлена именно из-за отключения агента;
- проекты клиентов остаются в текущем статусе и автоматически не переводятся в `Активен`.

### G) Admin Provider Leads XLSX (preview / commit)
Админский UI отправляет файл (**multipart** / `FormData`) -> `POST /admin/provider-leads-import/preview` -> `require_admin` -> `provider_leads_xlsx_import.create_preview`: проверка `.xlsx` и лимита размера -> на диск (временный каталог под `previewId`, TTL) кладётся копия файла и метаданные сессии -> в ответе **summary без записи** в `provider_leads` (валидность строк, дубли по `vid` в БД, проблемы привязки к проектам).

`POST /admin/provider-leads-import/commit` с тем же `previewId` -> снова `require_admin` и проверка, что preview создал **этот** админ -> повторный разбор и валидация на сервере -> запись лидов -> удаление артефактов preview; просроченные каталоги периодически чистятся.

Та же бизнес-логика строк вызывается из **CLI**: `tool_import_provider_leads_from_xlsx.py` (обход UI, для служебных сценариев).

### H) Project Create With Unique Name
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
- Notification routing / Telegram gate: `backend/app/notifications.py`
- Telegram-отправка: `backend/app/telegram.py`
- Debounce-воркер по `audit_events`: `backend/app/notify_worker.py`
- История успешных и неуспешных операций проектов: `backend/app/models.py` + `backend/app/schemas.py` + `backend/app/crud.py` + `backend/app/main.py`; фронт: `my-app-vite/src/api.ts` + `my-app-vite/src/components/ProjectHistoryModal.tsx` + `my-app-vite/src/components/AdminProjectHistoryModal.tsx` + `my-app-vite/src/components/ClientActivityHistory.tsx` + `my-app-vite/src/components/NotificationBell.tsx`
- Тарифы клиента (контур, который теперь зеркалит изменения в баланс клиента и содержит Telegram-сигналы остатка): `backend/app/models.py` + `backend/app/schemas.py` + `backend/app/crud.py` + `backend/app/main.py`; фронт: `my-app-vite/src/api.ts` + `my-app-vite/src/components/AdminBalance.tsx` + `my-app-vite/src/components/AdminClientsScreen.tsx` + `my-app-vite/src/components/TariffManagerModal.tsx`
- Агентский уровень доступа и владение клиентами: `backend/app/models.py` + `backend/app/schemas.py` + `backend/app/crud.py` + `backend/app/main.py`; фронт: `my-app-vite/src/App.tsx` + `my-app-vite/src/api.ts` + `my-app-vite/src/components/AdminClientsScreen.tsx` + `my-app-vite/src/components/AdminBalance.tsx` + `my-app-vite/src/components/Sidebar.tsx`
- Импорт provider leads из XLSX (админ preview/commit + общая логика с CLI): `backend/app/provider_leads_xlsx_import.py` + эндпоинты в `main.py`; фронт: `httpForm` / методы в `my-app-vite/src/api.ts`; CLI: `tool_import_provider_leads_from_xlsx.py`
- Экспорт provider leads: `tool_export_provider_leads.py`
- Проблемы времени/дат: `backend/app/time_utils.py` и места фильтрации в `main.py`
- Display-mapping кодов источника и имён проектов только во frontend: `my-app-vite/src/utils/sourceCodeDisplay.ts` + все UI-места, где показываются `dataSourceCode`, `source`, `project.name` и ошибки; backend/raw-коды не менять, если задача только про UI

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
- `NOTIFICATIONS_TELEGRAM_ENABLED` (глобальный флаг для всех Telegram-уведомлений backend)
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Google Sheets export:
- `GOOGLE_CREDENTIALS_FILE`
- `GOOGLE_SHEET_ID`
- `GOOGLE_SHEET_NAME`
- `LEADS_EXPORT_LOOKBACK_DAYS`

Импорт лидов провайдера из XLSX (админ / общий модуль):
- `PROVIDER_LEADS_IMPORT_PREVIEW_TTL_SECONDS` (TTL временных preview на диске)
- `PROVIDER_LEADS_IMPORT_MAX_FILE_BYTES` (макс. размер upload)

Дополнительно:
- `AUTO_LIMIT_CHECK_SECONDS`
- `SHEETS_TZ`
- `DEBOUNCE_WINDOW_MINUTES`
- `EXPORT_MAX_ROWS`

## 8) Encoding / Text Policy

- Repository text files are expected to be stored in `UTF-8`.
- Cyrillic is intentionally used across the project: UI labels, docs, messages, logs, and test data may contain Russian text.
- Do not replace Russian text with translit or Unicode escapes like `\u041f...` unless it is a short-term emergency workaround.
- If terminal output shows mojibake / broken Cyrillic, do not copy that text back into source files without verifying encoding first.
- When editing files with Cyrillic, preserve the existing file encoding and check diffs for broken text before finishing the task.

## 9) Known Pitfalls

1. Не полагаться только на frontend-проверки прав.
2. Не менять бизнес-правила только в UI, без `crud.py`.
3. При изменениях endpoint проверять `my-app-vite/src/api.ts` на совместимость.
4. Не ломать webhook-дедупликацию по `vid`.
5. Не убирать server-side защиту технических префиксов имени проекта: `B1_...B4_` и `[MB{id}]` для `unique_name_applied`.
6. Для клиентов с уникальными именами создание проекта не атомарно в одном внешнем вызове: после create нужен rename в Prostats с retry-логикой.
7. Учитывать, что часть логики сосредоточена в `main.py` и `crud.py`.
8. **XLSX preview/commit:** не ослаблять серверные проверки (тот же админ, валидный `previewId`, запрет commit при проблемной привязке к проектам) — UI только подсказывает.
9. Артефакты preview лежат в **локальном** каталоге на машине процесса backend; при нескольких инстансах без sticky session цепочка preview на одном узле и commit на другом **не сработает** — это ограничение текущей реализации.
10. При изменении логики удаления проектов учитывать двухэтапный матчинг webhook-лидов: активный проект по имени всегда приоритетнее удалённого в grace-окне; старые лиды могут ещё `48h` получать `project_id` удалённого проекта.
11. Для sync SQLAlchemy pool не держать request-scoped DB-сессию во время внешних вызовов `Prostats`/`Telegram`: рабочий шаблон — сначала read/snapshot и завершение транзакции, потом внешний вызов, потом короткая новая DB-сессия для локальной записи результата.
12. Не путать два разных контура: старый баланс клиента (`ClientBalanceOperation`) и тарифный учёт (`ClientTariff`, `ClientTariffOperation`). Сейчас тарифные операции зеркалятся в баланс клиента, поэтому тарифы влияют на `remaining`.
13. В админской таблице `Клиенты` и в сводке клиента `Текущий тариф` показывается последний созданный тариф, а не сумма всех тарифов клиента.
14. В manager-зоне нельзя полагаться только на UI-скрытие действий: агентские ограничения должны оставаться на backend. Агенту разрешён только read-only доступ к тарифам своих клиентов, а запись в тарифы и прямые операции баланса клиента должны оставаться закрытыми на backend.
15. При логике отключения/включения агента не путать два разных эффекта: блокировка редактирования проектов у клиентов и фактический статус самих проектов. При включении агента снимается только блокировка редактирования; проекты автоматически не возобновляются.
16. Старые balance-alert уведомления по расчётным дням/нулю удалены из runtime-логики. По остатку клиента в Telegram остаются только тарифные сигналы `signal1/signal2/signal3`.
17. Тарифные сигналы проверяются в периодическом лимит-контроле, а не в webhook провайдера и не в момент записи каждого нового лида. Если нужна мгновенная реакция, это уже изменение архитектуры, а не просто UI/API-правка.
18. При изменении логики тарифов не ломать инвариант пересечения порогов: если остаток упал сразу ниже нескольких сигналов, в Telegram должно уйти одно сообщение только по самому нижнему достигнутому сигналу.
19. Не путать display-коды frontend (`A` / `B` / `C` / `D`) и raw-коды backend (`B1` / `B2` / `B3` / `B4`). Если задача только про UI-переименование, менять нужно прежде всего `my-app-vite/src/utils/sourceCodeDisplay.ts`; полная замена raw-кодов — это уже отдельная миграция backend, БД и интеграций.
20. Не записывать неудачные проектные операции в `audit_events`: они начнут считаться успешными изменениями и попадут в админскую очередь pending. Для ошибок `create` / `update` / `delete` проектов использовать только `project_operation_events`; историю в UI объединять на уровне API.
