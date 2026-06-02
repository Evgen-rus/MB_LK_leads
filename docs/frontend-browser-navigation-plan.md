# План браузерной навигации для ЛК

Короткая рабочая памятка для перевода текущего React SPA на полноценную навигацию через URL и History API.

Цель: сохранить существующую архитектуру ЛК, но сделать поведение браузера ожидаемым:

- URL меняется при переходе между разделами;
- кнопки браузера **Назад** и **Вперёд** работают;
- обновление страницы сохраняет открытый раздел и основной контекст;
- ссылку на нужный раздел можно открыть в новой вкладке или отправить другому пользователю с подходящими правами.

## Что есть сейчас

Frontend уже является SPA:

- один React entrypoint;
- разделы переключаются через локальный `view` в `my-app-vite/src/App.tsx`;
- выбранный раздел дополнительно сохраняется в `localStorage` под ключом `last_view`;
- для `/login` уже используется `window.history.replaceState(...)`;
- тяжёлые manager-разделы подгружаются через `React.lazy`.

Проблема:

- при обычных переходах между разделами URL не меняется;
- новые записи в истории браузера не создаются;
- кнопка браузера **Назад** остаётся неактивной;
- контекст drilldown-переходов хранится только в React state и частично в `localStorage`.

## Почему красивые URL можно внедрить

Продакшен-конфиг nginx уже содержит SPA fallback:

```nginx
location / {
    root /opt/MB_LK_leads/my-app-vite/dist;
    index index.html index.htm;
    try_files $uri $uri/ /index.html;
}
```

Поэтому прямое открытие `/projects`, `/leads` или `/admin/clients` должно возвращать `index.html`, после чего frontend сам восстановит нужный экран.

## Объём первой версии

В первую версию включить:

- URL для всех верхнеуровневых экранов;
- `pushState` при пользовательских переходах;
- `replaceState` при системных исправлениях URL;
- обработчик `popstate`;
- query-параметры для важного drilldown-контекста;
- fallback при неизвестном URL или отсутствии прав;
- сохранение существующего lazy loading.

Не включать в первую версию:

- открытие модальных окон через URL;
- сохранение всех локальных фильтров таблиц в query-параметры;
- перенос фильтров админского дашборда из `localStorage` в URL;
- добавление React Router, если History API покрывает задачу без лишней зависимости.

## Целевые URL

### Общие экраны

| Текущий `view` | URL |
| --- | --- |
| `projects` | `/projects` |
| `leads` | `/leads` |
| `reports` | `/reports` |
| `balance` | `/balance` |
| `blacklist` | `/blacklist` |
| `support` | `/support` |
| `integrations` | `/integrations` |
| `education` | `/education` |
| `onboarding` | `/onboarding` |

### Клиент

| Текущий `view` | URL |
| --- | --- |
| `client-dashboard` | `/dashboard` |

### Админ

| Текущий `view` | URL |
| --- | --- |
| `admin-dashboard` | `/admin/dashboard` |
| `admin-clients` | `/admin/clients` |
| `agents` | `/admin/agents` |
| `admin-provider-import` | `/admin/provider-leads-import` |
| `activity` | `/admin/activity` |

### Агент

Агент использует manager-зону, но URL должен оставаться нейтральным:

| Текущий `view` | URL |
| --- | --- |
| `admin-clients` | `/manager/clients` |
| `activity` | `/manager/activity` |

Для общих экранов агента использовать те же URL: `/projects`, `/leads`, `/reports`, `/blacklist`.

## Query-параметры первой версии

### Проекты manager-зоны

```text
/projects?clientId=193
/projects?clientId=193&tab=changes
/projects?clientId=193&tab=blacklist-changes
```

Правила:

- `clientId` восстанавливает выбранного клиента;
- `tab=projects` можно не указывать;
- `tab=changes` открывает изменения проектов клиента;
- `tab=blacklist-changes` открывает изменения чёрного списка клиента.

### Идентификации

```text
/leads?clientId=193
/leads?clientId=193&projectId=42
/leads?projectId=42&from=2026-06-01&to=2026-06-02
/leads?unlinked=1
```

Правила:

- для клиента `clientId` игнорируется;
- для manager-роли `clientId` ограничивает клиента;
- `projectId` предзаполняет проект;
- `from` / `to` восстанавливают период;
- `unlinked=1` открывает непривязанные лиды для админа.

### Баланс

```text
/balance?clientId=193
/balance?clientId=193&modal=tariff
```

Правила:

- `clientId` выбирает клиента в manager-зоне;
- `modal=tariff` открывает менеджер тарифов;
- клиентский `/balance` не использует `clientId`.

### Чёрный список

```text
/blacklist?clientId=193
```

Правило:

- `clientId` применяется только в manager-зоне.

### Клиенты админа

```text
/admin/clients?focusClientId=193
```

Правило:

- `focusClientId` заменяет текущую служебную запись `admin_clients_focus_id` в `localStorage` для переходов из дашборда.

## Архитектура frontend-навигации

Создать отдельный модуль:

```text
my-app-vite/src/utils/navigation.ts
```

Он должен содержать:

1. Тип контекста навигации.
2. Таблицу `view -> pathname`.
3. Разбор `window.location.pathname` и `window.location.search`.
4. Сборку URL из `view` и контекста.
5. Валидацию query-параметров.
6. Fallback для неизвестных URL.

Предлагаемый тип:

```ts
type NavigationState = {
  view: ViewType;
  clientId?: number;
  clientName?: string;
  projectId?: number;
  from?: string;
  to?: string;
  unlinked?: boolean;
  projectsTab?: 'projects' | 'changes' | 'blacklist-changes';
  balanceModal?: 'tariff';
  focusClientId?: number;
};
```

`clientName` можно хранить только в `history.state`, но не в URL. После прямого открытия ссылки имя подтянется экраном по `clientId`.

## Изменения в `App.tsx`

### 1. Ввести единый helper перехода

Заменить прямые вызовы:

```ts
setView('projects');
localStorage.setItem(STORAGE_VIEW_KEY, 'projects');
```

на один helper:

```ts
navigate({ view: 'projects', clientId, projectsTab: 'projects' });
```

Helper:

- собирает URL;
- вызывает `window.history.pushState(...)`;
- обновляет `view`;
- обновляет связанный React state;
- при необходимости сохраняет `last_view` для обратной совместимости.

### 2. Добавить восстановление из URL

При первом открытии:

1. разобрать URL;
2. проверить доступность экрана для текущей роли;
3. восстановить `view` и query-контекст;
4. если URL пустой `/`, использовать сохранённый `last_view` или ролевой default;
5. заменить `/` на фактический URL через `replaceState`.

### 3. Добавить `popstate`

Подписаться на:

```ts
window.addEventListener('popstate', ...)
```

При событии:

- разобрать текущий URL;
- восстановить `view`;
- восстановить контекст выбранного клиента, проекта и периода;
- не создавать новую запись истории.

### 4. Разделить push и replace

Использовать `pushState`:

- клики по sidebar;
- переходы из дашборда;
- drilldown-переходы из таблиц;
- переходы из колокольчика.

Использовать `replaceState`:

- успешный login;
- logout и переход на `/login`;
- исправление URL при отсутствии прав;
- неизвестный URL;
- нормализация пустого `/`.

## Ролевые правила

URL не является механизмом доступа. Backend-проверки остаются обязательными.

Frontend должен мягко исправлять недоступные маршруты:

- клиент открыл `/admin/dashboard` -> перенаправить на `/dashboard`;
- агент открыл `/admin/dashboard` -> перенаправить на `/manager/clients`;
- неавторизованный пользователь открыл любой защищённый URL -> заменить URL на `/login`;
- после успешного логина открыть ролевой default, если до логина не сохранялся допустимый исходный URL.

Ролевые default:

| Роль | URL |
| --- | --- |
| `client` | `/dashboard` |
| `agent` | `/manager/clients` |
| `admin` | `/admin/dashboard` |

## Что делать с `localStorage`

На первом этапе не удалять `last_view`, чтобы сохранить обратную совместимость.

Приоритет восстановления:

1. валидный URL;
2. `last_view`, если открыт `/`;
3. ролевой default.

После стабилизации можно удалить `last_view` отдельной задачей.

Фильтры админского дашборда пока оставить в `localStorage`: они не мешают браузерной истории разделов.

## Изменяемые файлы

Основные:

- `my-app-vite/src/App.tsx`
- `my-app-vite/src/components/Sidebar.tsx`
- новый `my-app-vite/src/utils/navigation.ts`

Вероятно понадобятся точечные изменения:

- `my-app-vite/src/components/AdminDashboard.tsx`
- `my-app-vite/src/components/ClientDashboard.tsx`
- `my-app-vite/src/components/AdminClientsScreen.tsx`
- `my-app-vite/src/components/AdminAgentsScreen.tsx`
- `my-app-vite/src/components/AdminProjectsScreen.tsx`
- `my-app-vite/src/components/ProjectsTable.tsx`
- `my-app-vite/src/components/NotificationBell.tsx`
- `my-app-vite/src/components/AdminActivityBell.tsx`

Backend менять не требуется.

## Порядок реализации

### Шаг 1. Создать `navigation.ts`

- описать route table;
- реализовать parse/build;
- добавить unit-подобную проверку чистых функций;
- не менять компоненты.

### Шаг 2. Подключить верхнеуровневые переходы

- добавить `navigate(...)` в `App.tsx`;
- заменить переходы sidebar;
- добавить первичное восстановление URL;
- добавить `popstate`.

Результат:

- URL меняется;
- **Назад** / **Вперёд** работают между разделами;
- refresh сохраняет раздел.

### Шаг 3. Перевести drilldown-переходы

- клиентский дашборд;
- админский дашборд;
- `Клиенты`;
- `Агенты`;
- manager-проекты;
- переходы к идентификациям;
- баланс и тарифы;
- чёрный список;
- колокольчики.

Результат:

- URL отражает основной контекст;
- refresh и **Назад** восстанавливают выбранного клиента и проект.

### Шаг 4. Добавить ролевые fallback

- неизвестный маршрут;
- недоступный маршрут;
- прямое открытие URL без токена;
- login/logout;
- impersonation и смена токена в другой вкладке.

### Шаг 5. Проверить вручную и через Playwright

Добавить E2E-сценарии:

1. Переход `/dashboard -> /projects -> /leads`.
2. Кнопка **Назад** возвращает `/projects`.
3. Кнопка **Вперёд** возвращает `/leads`.
4. Refresh на `/projects` сохраняет экран.
5. Прямое открытие `/leads?projectId=...` восстанавливает фильтр.
6. Прямое открытие manager URL восстанавливает `clientId`.
7. Клиент не может остаться на `/admin/dashboard`.
8. Без токена защищённый URL заменяется на `/login`.

## Риски

### 1. Дублирование state и URL

Риск:

- React state и адресная строка могут разойтись.

Защита:

- все переходы проводить только через `navigate(...)`;
- запретить новые прямые `setView(...)` вне навигационного helper и системного восстановления.

### 2. Потеря drilldown-контекста

Риск:

- после refresh выбранный клиент восстановится, но имя клиента временно будет пустым.

Защита:

- считать `clientId` источником истины;
- имя подтягивать экраном или хранить вспомогательно в `history.state`.

### 3. Роли и impersonation

Риск:

- URL от предыдущей роли может оказаться недоступен после входа под другой ролью.

Защита:

- валидировать маршрут после определения роли;
- применять ролевой fallback через `replaceState`.

### 4. Login redirect

Риск:

- прямое открытие ссылки без токена потеряется после login.

Решение для первой версии:

- сохранить исходный защищённый URL перед заменой на `/login`;
- после успешного входа открыть его, если он допустим для роли;
- иначе открыть ролевой default.

### 5. Query-параметры не заменяют backend-права

Риск:

- пользователь вручную подставит чужой `clientId`.

Защита:

- frontend может показать ошибку или сбросить фильтр;
- backend продолжает ограничивать данные по роли и владельцу.

## Критерии готовности

Работа считается завершённой, если:

- sidebar меняет URL;
- браузерные **Назад** и **Вперёд** переключают экраны без перезагрузки;
- refresh сохраняет экран;
- прямое открытие поддерживаемого URL работает;
- drilldown из дашбордов и manager-таблиц сохраняет основной контекст в query;
- login/logout не ломаются;
- недоступные роли не могут остаться на чужих маршрутах;
- lazy loading админских экранов сохранён;
- `npm run build`, `npm run lint` и Playwright-сценарии проходят.

## Оценка

Полноценный вариант: ориентировочно `1–2 рабочих дня`.

Основная сложность не в URL как таковых, а в аккуратном переносе всех существующих внутренних переходов и восстановлении контекста после refresh и `popstate`.

