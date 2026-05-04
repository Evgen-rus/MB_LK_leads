# Release Notes — 04.05.2026

## Кратко

- Для проектов с источником **СМС** снят запрет на редактирование в ЛК для клиента и администратора.
- Обновление и ручное переключение статуса `Активен ↔ На паузе` для **СМС**-проектов теперь выполняются только локально: backend не отправляет `update`-запрос в Prostats и не ждёт ответ провайдера.
- Автопауза по лимитам, admin collection `pause/resume` и связанные фоновые смены статуса для **СМС**-проектов тоже переведены в локальный режим без вызова Prostats.
- Новые успешные audit-события `create/update/delete` по не-`СМС` проектам теперь автоматически стартуют как `Выполнено`; в pending-бейджах админского ЛК по проектам остаются только `СМС`-события.
- Удаление **СМС**-проектов не менялось: оно по-прежнему идёт через провайдера.

- В зоне администратора и агента добавлена **общая история действий** по доступным клиентам (аналог клиентской ленты, но с фильтром по клиенту).
- В шапке для менеджеров показывается **отдельный колокольчик**: последние события по всем доступным клиентам.
- В меню для ролей **админ** и **агент** добавлен пункт **«История изменений»**.
- Лента формируется на backend с учётом прав: агент видит только своих клиентов (`owner_agent_id`).
- В ответе списка событий добавлены поля **клиент** и **статус обработки** (`pending` / `done`) для аудита; ошибки проектных операций по-прежнему без очереди админской обработки.

## Что изменено

### История действий в админском ЛК

- Endpoint `GET /admin/activity/events`: пагинация, диапазон дат, фильтр по `clientId`, типам сущностей (`project`, `blacklist`, `balance`, `report`), поиск `q`, фильтр `status` (`all`, `success`, `failed`, `pending`, `done`).
- Источники данных те же, что у клиентской ленты: `audit_events`, `project_operation_events` (неуспешные операции), `client_balance_operations`, `report_exports`.
- Клиентская лента (`GET /activity/events`) сохранена; сборка вынесена в общий helper `list_activity_events_for_clients` в `backend/app/crud.py`.

### UI

- Компонент **AdminActivityBell**: последние 20 событий, отображение клиента, результата и при необходимости метки «Не выполнено», переход на полный экран истории.
- Компонент **AdminActivityHistory**: фильтры по датам, клиенту, типу события, статусу и поиску; карточка проекта по кнопке как у клиента.
- В **App.tsx**: для менеджеров колокольчик и экран `activity` подключены к админской истории; клиентский UX без изменений.

### Схемы API

- В `ActivityEventOut` добавлены необязательные поля `client` и `status` (`pending` | `done`) для списков истории в manager-зоне.

## Технически

- `backend/app/crud.py`: `_load_client_info_map`, `_activity_status_matches`, `list_activity_events_for_clients`, `list_client_activity_events` вызывает helper.
- `backend/app/main.py`: маршрут `GET /admin/activity/events`, проверка доступа через `_ensure_manager_client_access` и `get_accessible_client_ids_for_manager`.
- `backend/app/schemas.py`: расширение `ActivityEventOut`.
- Frontend: `my-app-vite/src/api.ts` (`fetchAdminActivityEvents`), `AdminActivityBell.tsx`, `AdminActivityHistory.tsx`, правки `App.tsx`, `Sidebar.tsx`.
- Проверки: `python -m py_compile` для изменённых модулей backend, `npm run build`.

