# План браузерной навигации ЛК через React Router

Альтернативный план внедрения полноценной браузерной навигации через React Router.

Исходный документ `docs/frontend-browser-navigation-plan.md` остаётся отдельным вариантом реализации через History API и не изменяется.

## Решение

Для текущего ЛК использовать React Router в **Declarative Mode**:

```text
react-router
```

Основные инструменты:

- `<BrowserRouter>`
- `<Routes>`
- `<Route>`
- `<Navigate>`
- `<NavLink>`
- `useNavigate()`
- `useLocation()`
- `useSearchParams()`

Не переходить сразу на Data Mode с `createBrowserRouter()` и `loader/action`.

Причина:

- проект уже имеет собственный API-клиент;
- данные загружаются внутри существующих компонентов;
- задача касается прежде всего URL и браузерной истории;
- Declarative Mode позволяет внедрить маршруты постепенно без переработки backend и бизнес-логики экранов.

## Цель

После внедрения:

- URL меняется при переходе между разделами;
- кнопки браузера **Назад** и **Вперёд** работают;
- refresh сохраняет открытый раздел и основной контекст;
- прямую ссылку на раздел можно открыть в новой вкладке;
- drilldown-переходы из дашборда и таблиц отражаются в query-параметрах;
- frontend проверяет доступность маршрутов по роли;
- backend по-прежнему остаётся источником истины для прав доступа.

## Почему React Router подходит проекту

Сейчас frontend уже является SPA:

- один React entrypoint;
- разделы переключаются через локальный `view` в `my-app-vite/src/App.tsx`;
- тяжёлые manager-экраны подключаются через `React.lazy`;
- nginx уже содержит SPA fallback:

```nginx
location / {
    root /opt/MB_LK_leads/my-app-vite/dist;
    index index.html index.htm;
    try_files $uri $uri/ /index.html;
}
```

React Router добавляет стандартный слой навигации поверх существующего SPA:

- не требует backend-маршрутов для страниц;
- не ломает lazy loading;
- не требует ручной подписки на `popstate`;
- снижает риск рассинхронизации URL и React-state.

## Что не делать в первой версии

- не переносить API-загрузку в route loaders;
- не внедрять Framework Mode;
- не переносить все локальные фильтры таблиц в URL;
- не открывать модальные окна через route segments;
- не переписывать backend;
- не убирать `localStorage.last_view` до стабилизации новой навигации.

## Установка

Добавить зависимость:

```powershell
cd my-app-vite
npm install react-router
```

Изменятся:

- `my-app-vite/package.json`
- `my-app-vite/package-lock.json`

## Целевые URL

### Общие экраны

| Экран | URL |
| --- | --- |
| Проекты | `/projects` |
| Идентификации | `/leads` |
| Отчёты | `/reports` |
| Баланс | `/balance` |
| Чёрный список | `/blacklist` |
| Техподдержка | `/support` |
| Интеграции | `/integrations` |
| Обучение | `/education` |
| Онбординг | `/onboarding` |

### Клиент

| Экран | URL |
| --- | --- |
| Дашборд | `/dashboard` |

### Админ

| Экран | URL |
| --- | --- |
| Дашборд | `/admin/dashboard` |
| Клиенты | `/admin/clients` |
| Агенты | `/admin/agents` |
| Импорт лидов | `/admin/provider-leads-import` |
| История изменений | `/admin/activity` |

### Агент

| Экран | URL |
| --- | --- |
| Клиенты | `/manager/clients` |
| История изменений | `/manager/activity` |

Для общих экранов агента использовать `/projects`, `/leads`, `/reports`, `/blacklist`.

## Query-параметры

### Проекты manager-зоны

```text
/projects?clientId=193
/projects?clientId=193&tab=changes
/projects?clientId=193&tab=blacklist-changes
```

### Идентификации

```text
/leads?clientId=193
/leads?clientId=193&projectId=42
/leads?projectId=42&from=2026-06-01&to=2026-06-02
/leads?unlinked=1
```

### Баланс

```text
/balance?clientId=193
/balance?clientId=193&modal=tariff
```

### Чёрный список

```text
/blacklist?clientId=193
```

### Клиенты админа

```text
/admin/clients?focusClientId=193
```

## Новая структура frontend

### 1. Подключить `<BrowserRouter>`

В `my-app-vite/src/main.tsx` обернуть приложение:

```tsx
import { BrowserRouter } from 'react-router';

createRoot(document.getElementById('root')!).render(
  <BrowserRouter>
    <App />
  </BrowserRouter>,
);
```

### 2. Добавить конфигурацию маршрутов

Создать:

```text
my-app-vite/src/router/routes.ts
```

Файл хранит:

- константы путей;
- типы query-параметров;
- helper для безопасного разбора чисел, дат и enum-параметров;
- ролевые default URL;
- таблицу доступности маршрутов по роли.

Пример констант:

```ts
export const ROUTES = {
  clientDashboard: '/dashboard',
  projects: '/projects',
  leads: '/leads',
  reports: '/reports',
  balance: '/balance',
  blacklist: '/blacklist',
  adminDashboard: '/admin/dashboard',
  adminClients: '/admin/clients',
  adminAgents: '/admin/agents',
  managerClients: '/manager/clients',
} as const;
```

### 3. Разделить shell и route content

Оставить в `App.tsx`:

- auth-проверку;
- роль;
- header;
- sidebar;
- toast;
- общие модальные окна;
- lazy imports;
- общий layout.

Вынести выбор экрана по URL в отдельный компонент:

```text
my-app-vite/src/router/AppRoutes.tsx
```

Пример структуры:

```tsx
<Routes>
  <Route path="/dashboard" element={<ClientOnly><ClientDashboard /></ClientOnly>} />
  <Route path="/projects" element={<ProjectsRoute />} />
  <Route path="/leads" element={<LeadsRoute />} />
  <Route path="/admin/dashboard" element={<AdminOnly><AdminDashboard /></AdminOnly>} />
  <Route path="/admin/clients" element={<ManagerOnly><AdminClientsScreen /></ManagerOnly>} />
  <Route path="*" element={<RoleDefaultRedirect />} />
</Routes>
```

`ClientOnly`, `AdminOnly`, `ManagerOnly`:

- проверяют только frontend-доступ к экрану;
- при отсутствии доступа возвращают `<Navigate replace ... />`;
- не заменяют backend-проверки доступа.

### 4. Перевести sidebar на `<NavLink>`

Сейчас `Sidebar.tsx` вызывает:

```tsx
onNavigate('projects')
```

После миграции:

```tsx
<NavLink to="/projects">Проекты</NavLink>
```

Активное состояние брать из `NavLink`, а не из `view`.

Для переходов с query-параметрами использовать `useNavigate()`.

## Работа с текущим `view`

Сейчас `App.tsx` использует:

```ts
const [view, setView] = useState<ViewType>(...)
```

После миграции URL становится источником истины.

Варианты:

1. Постепенный переход: временно вычислять `view` из `useLocation()` для заголовка и совместимости.
2. Финальный переход: убрать mutable `view` и использовать route match.

Рекомендуемый порядок:

- сначала добавить helper `getViewFromPathname(pathname, role)`;
- заменить прямые `setView(...)` на `navigate(...)`;
- после стабилизации удалить state `view`.

## Программные переходы

Заменить прямые вызовы:

```ts
setView('leads');
localStorage.setItem('last_view', 'leads');
```

на:

```ts
navigate('/leads');
```

Для drilldown:

```ts
navigate(`/leads?clientId=${clientId}&projectId=${projectId}&from=${fromDate}&to=${toDate}`);
```

Чтобы не собирать строки вручную, добавить helper:

```ts
buildUrl('/leads', {
  clientId,
  projectId,
  from: fromDate,
  to: toDate,
});
```

## Где заменить переходы

Основное место:

- `my-app-vite/src/App.tsx`

Там сейчас около 25 прямых вызовов `setView(...)`.

Переходы есть в сценариях:

- sidebar;
- login;
- logout;
- колокольчик;
- клиентский дашборд;
- админский дашборд;
- список клиентов;
- список агентов;
- manager-проекты;
- переход из проектов к идентификациям;
- переход к балансу;
- переход к чёрному списку.

## Восстановление контекста после refresh

Route-компоненты читают query через:

```ts
const [searchParams] = useSearchParams();
```

Примеры:

```ts
const clientId = parsePositiveInt(searchParams.get('clientId'));
const projectId = parsePositiveInt(searchParams.get('projectId'));
const unlinked = searchParams.get('unlinked') === '1';
```

Затем передают существующим экранам:

```tsx
<AdminProjectsScreen
  initialClientId={clientId}
  initialFocus={projectsTab}
/>
```

Таким образом сохраняются текущие интерфейсы компонентов и снижается объём правок.

## Login и logout

### Неавторизованный пользователь

Если пользователь открывает:

```text
/projects?clientId=193
```

без токена:

1. сохранить исходный URL в `sessionStorage`;
2. перейти на `/login` через `navigate('/login', { replace: true })`;
3. после успешного входа проверить допустимость исходного URL для роли;
4. открыть исходный URL либо ролевой default.

### Logout

При выходе:

- очистить auth-state;
- перейти на `/login` через `replace`;
- очистить сохранённый URL возврата.

## Ролевые default URL

| Роль | URL |
| --- | --- |
| `client` | `/dashboard` |
| `agent` | `/manager/clients` |
| `admin` | `/admin/dashboard` |

## `localStorage.last_view`

На время миграции оставить.

Приоритет восстановления:

1. текущий валидный URL;
2. сохранённый URL возврата после login;
3. `localStorage.last_view`, только если открыт `/`;
4. ролевой default.

После стабилизации:

- удалить использование `last_view`;
- оставить URL единственным источником истины для раздела.

## Lazy loading

Существующие lazy imports сохранить:

```tsx
const AdminDashboard = lazy(() => import('./components/AdminDashboard'));
```

Маршрутизация не должна возвращать статические runtime-импорты manager-экранов.

Это важно: клиентский ЛК не должен скачивать тяжёлые админские чанки при старте.

## Изменяемые файлы

Основные:

- `my-app-vite/package.json`
- `my-app-vite/package-lock.json`
- `my-app-vite/src/main.tsx`
- `my-app-vite/src/App.tsx`
- `my-app-vite/src/components/Sidebar.tsx`
- новый `my-app-vite/src/router/routes.ts`
- новый `my-app-vite/src/router/AppRoutes.tsx`

Вероятны точечные изменения:

- `my-app-vite/src/components/AdminDashboard.tsx`
- `my-app-vite/src/components/ClientDashboard.tsx`
- `my-app-vite/src/components/AdminClientsScreen.tsx`
- `my-app-vite/src/components/AdminAgentsScreen.tsx`
- `my-app-vite/src/components/AdminProjectsScreen.tsx`
- `my-app-vite/src/components/ProjectsTable.tsx`
- `my-app-vite/src/components/NotificationBell.tsx`
- `my-app-vite/src/components/AdminActivityBell.tsx`
- `my-app-vite/e2e/app.spec.ts`

Backend менять не требуется.

## Порядок реализации

### Шаг 1. Установить React Router

- добавить `react-router`;
- убедиться, что `npm run build` проходит;
- не менять поведение экранов.

### Шаг 2. Создать route helpers

- добавить `router/routes.ts`;
- описать константы URL;
- реализовать `buildUrl(...)`;
- реализовать безопасный parse query;
- описать role defaults.

### Шаг 3. Подключить `<BrowserRouter>`

- обернуть `<App />`;
- добавить route shell;
- временно вычислять `view` из URL для заголовка и совместимости.

### Шаг 4. Перевести sidebar

- заменить клики на `<NavLink>`;
- проверить active-state;
- проверить **Назад** / **Вперёд**.

### Шаг 5. Перевести внутренние переходы

- login/logout;
- колокольчики;
- клиентский дашборд;
- админский дашборд;
- клиенты;
- агенты;
- проекты;
- идентификации;
- баланс;
- чёрный список.

### Шаг 6. Восстановить query-контекст

- `clientId`;
- `projectId`;
- `from`;
- `to`;
- `unlinked`;
- `tab`;
- `modal`;
- `focusClientId`.

### Шаг 7. Удалить лишний mutable state

- убрать прямые вызовы `setView(...)`;
- убрать дублирование `view` и URL;
- оставить совместимость `last_view` только на переходный период.

### Шаг 8. Проверить вручную и через Playwright

## E2E-сценарии

Добавить проверки:

1. Sidebar меняет `/dashboard -> /projects -> /leads`.
2. **Назад** возвращает `/projects`.
3. **Вперёд** возвращает `/leads`.
4. Refresh на `/projects` сохраняет экран.
5. Прямое открытие `/leads?projectId=...` восстанавливает фильтр.
6. Переход из проектов к идентификациям формирует query с `projectId`, `from`, `to`.
7. Переход из админского дашборда в проекты формирует `clientId`.
8. Переход из клиентов в баланс формирует `clientId` и `modal=tariff`.
9. Клиент при открытии `/admin/dashboard` получает `/dashboard`.
10. Агент при открытии `/admin/dashboard` получает `/manager/clients`.
11. Без токена защищённый URL заменяется на `/login`.
12. После login допустимый исходный URL восстанавливается.
13. Logout заменяет URL на `/login`.

## Риски

### 1. Слишком большая правка `App.tsx`

Риск:

- одновременный перенос всех экранов усложнит ревью.

Защита:

- внедрять по шагам;
- сначала подключить router shell;
- затем переводить переходы группами;
- после каждого этапа запускать build и lint.

### 2. Рассинхронизация URL и старого `view`

Риск:

- часть кода продолжит менять `view` напрямую.

Защита:

- временно вычислять `view` только из `useLocation()`;
- заменить все прямые `setView(...)`;
- проверить поиском `rg -n "setView\\(" my-app-vite/src`.

### 3. Query-параметры могут содержать мусор

Риск:

- пользователь вручную передаст отрицательный `clientId` или неверную дату.

Защита:

- использовать безопасные parse helpers;
- игнорировать невалидные значения;
- не считать URL механизмом авторизации.

### 4. Роли и impersonation

Риск:

- URL от предыдущей роли недоступен после смены токена.

Защита:

- повторно валидировать маршрут при смене auth-state;
- использовать `<Navigate replace />` к ролевому default.

### 5. Query-контекст восстанавливается не полностью

Риск:

- после refresh экран открыт правильно, но выбранный клиент или период потерян.

Защита:

- считать URL источником истины для основного контекста;
- передавать query в существующие `initial*` props;
- добавить E2E для прямого открытия URL.

## Критерии готовности

Работа завершена, если:

- React Router подключён в Declarative Mode;
- sidebar использует `<NavLink>`;
- программные переходы используют `useNavigate`;
- URL меняется при переходах;
- **Назад** и **Вперёд** работают без перезагрузки;
- refresh сохраняет экран и основной query-контекст;
- прямые ссылки работают;
- login/logout работают;
- ролевые fallback работают;
- lazy loading админских экранов сохранён;
- backend не изменён;
- `npm run build`, `npm run lint` и Playwright-проверки проходят.

## Оценка

Полноценное внедрение: ориентировочно `1–2 рабочих дня`.

React Router предпочтительнее ручного History API: он уменьшает объём собственного инфраструктурного кода и делает дальнейшее расширение навигации предсказуемее.

