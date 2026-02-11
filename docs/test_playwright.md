# Playwright — инструкция по тестированию MB_LK_leads

Документ описывает два способа тестирования приложения: **автоматические E2E-тесты** (Playwright) и **интерактивное тестирование через чат Cursor** (встроенный Browser Agent).

---

## Содержание

1. [Проверка установки (перед стартом)](#1-проверка-установки-перед-стартом)
2. [Быстрый старт](#2-быстрый-старт)
3. [Playwright: автотесты в проекте](#3-playwright-автотесты-в-проекте)
4. [Тестирование через Cursor Chat (@browser)](#4-тестирование-через-cursor-chat-browser)
5. [Git и деплой](#5-git-и-деплой)
6. [Частые вопросы](#6-частые-вопросы)

---

## 1. Проверка установки (перед стартом)

### Проверка пакета Playwright

**Через package.json:**  
Файл `my-app-vite/package.json` → `devDependencies` → должна быть строка `"@playwright/test"`.

**Через терминал:**
```powershell
cd my-app-vite
npm list @playwright/test
```

Установлено — будет что-то вроде `@playwright/test@1.58.2`. Если нет — установи:
```powershell
cd my-app-vite
npm install -D @playwright/test
```

### Проверка версии Playwright

```powershell
cd my-app-vite
npx playwright --version
```

Вывод версии (например, `Version 1.58.2`) означает, что пакет установлен.

### Проверка браузеров (Chromium)

```powershell
cd my-app-vite
npx playwright install --dry-run
```

Покажет, какие браузеры уже есть, какие нужно скачать. Если Chromium не установлен:
```powershell
cd my-app-vite
npx playwright install chromium
```

### Проверка папки Chromium вручную (Windows)

```powershell
dir $env:LOCALAPPDATA\ms-playwright
```

Если есть подпапка `chromium-*` (например, `chromium-1208`) — Chromium установлен.  
Папка: `C:\Users\<имя>\AppData\Local\ms-playwright`.

---

## 2. Быстрый старт

**Перед запуском тестов:**
- Бэкенд: `uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000`
- Фронтенд: `cd my-app-vite && npm run dev` (порт 5173)

**Запуск тестов:**
```bash
cd my-app-vite
npm run test:e2e
```

8 тестов выполнится без дополнительной настройки. 3 теста пропустятся, если не заданы `E2E_LOGIN` и `E2E_PASSWORD`.

---

## 3. Playwright: автотесты в проекте

### Что это такое

**Playwright** — библиотека для E2E-тестов. Она управляет браузером (Chromium), заполняет формы, кликает по элементам и проверяет ожидаемый результат. Это автоматическая замена ручной проверки.

### Структура в проекте

| Файл / папка | Назначение |
|--------------|------------|
| `my-app-vite/playwright.config.ts` | Конфигурация: URL, браузер, webServer |
| `my-app-vite/e2e/app.spec.ts` | Описание тестов |
| `my-app-vite/package.json` | Скрипт `test:e2e`, зависимость `@playwright/test` |

### Что проверяют тесты

| Категория | Проверки |
|-----------|-----------|
| **Форма логина** | Поля видны, кнопка «Войти» недоступна при пустых полях, активна при заполненных, ошибка при неверных учётных данных |
| **Консоль** | Нет JavaScript-ошибок при загрузке страницы логина |
| **Responsive** | Форма логина отображается на 375px и 768px |
| **Воркфлоу** (с учётными данными) | Переходы по sidebar, заполнение формы «Создать проект» |

### Запуск с учётными данными

Для тестов, которые логинятся и проверяют воркфлоу после входа:

**PowerShell (Windows):**
```powershell
$env:E2E_LOGIN="ваш_логин"
$env:E2E_PASSWORD="ваш_пароль"
cd my-app-vite
npm run test:e2e
```

**Linux / macOS:**
```bash
E2E_LOGIN=ваш_логин E2E_PASSWORD=ваш_пароль npm run test:e2e
```

### Исправление ошибки TypeScript «Cannot find name 'process'»

В начале файла `e2e/app.spec.ts` уже добавлена директива:
```ts
/// <reference types="node" />
```
Она подключает типы Node.js, в которых определено `process.env`.

---

## 4. Тестирование через Cursor Chat (@browser)

Помимо автотестов, Cursor умеет тестировать приложение **интерактивно** через встроенный браузер. Это не заменяет Playwright, а дополняет: быстрая проверка «на лету», визуальный аудит, отладка по скриншотам.

**Документация:** [Cursor — Browser (Automated testing)](https://cursor.com/docs/agent/browser#automated-testing)

### Как использовать

В чате Cursor укажи `@browser` и напиши, что нужно проверить. Agent откроет браузер, перейдёт на нужный URL и выполнит действия.

### Примеры промптов для MB_LK_leads

| Задача | Пример промпта |
|--------|----------------|
| Заполнение форм | `@browser Заполни форму логина тестовыми данными test/test и проверь, что появилась ошибка` |
| Проверка воркфлоу | `@browser Открой localhost:5173, залогинься, перейди в раздел Проекты и нажми «Добавить проект»` |
| Responsive | `@browser Открой форму логина на localhost:5173 и проверь, как она выглядит на мобильной ширине экрана` |
| Валидация ошибок | `@browser Заполни форму логина неверными данными и убедись, что отображается сообщение об ошибке` |
| Мониторинг консоли | `@browser Открой localhost:5173, залогинься и проверь консоль браузера на JavaScript-ошибки` |
| Скриншот состояния | `@browser Открой localhost:5173, сделай скриншот страницы логина` |

### Важно перед запуском @browser

1. **Серверы должны быть запущены:**
   - Фронтенд: `http://localhost:5173`
   - Бэкенд: `http://localhost:8000`

2. **Настройки браузера в Cursor:**
   - `Cursor Settings` → `Tools & MCP` → `Browser Automation`
   - Проверь, что Browser включён и выбран режим (Chrome или Browser Tab)

3. **Одобрение действий:** по умолчанию Agent запрашивает подтверждение каждого действия в браузере. Это можно изменить в `Agent Settings`.

### Отличие от Playwright

| | Playwright | @browser (Cursor) |
|---|------------|-------------------|
| **Когда** | По команде `npm run test:e2e` | По запросу в чате |
| **Как** | Код в `e2e/app.spec.ts` | Интерактивно, Agent сам решает действия |
| **Результат** | Отчёт: X passed, Y failed | Скриншоты, ответ в чате |
| **Для чего** | Регрессии, CI/CD, стабильные сценарии | Быстрая проверка, исследование, визуальный аудит |

---

## 5. Git и деплой

### Что коммитить

- `my-app-vite/package.json`
- `my-app-vite/package-lock.json`
- `my-app-vite/playwright.config.ts`
- `my-app-vite/e2e/`
- `my-app-vite/.gitignore` (с исключением `test-results/`, `playwright-report/`)

### На сервере

```bash
cd /путь/к/MB_LK_leads
git pull origin main
cd my-app-vite
npm install
```

Тесты на сервере обычно не нужны. Playwright не влияет на работу сайта: это инструмент проверки, он не участвует в `npm run build` и отдаче статики.

---

## 6. Частые вопросы

**Нужно ли запускать тесты на сервере?**  
Обычно нет. Достаточно прогонять их локально перед push.

**Сломает ли Playwright сайт на сервере после `npm install`?**  
Нет. Playwright — dev-зависимость, на работу приложения не влияет.

**Можно ли добавлять новые тесты?**  
Да. Файл `my-app-vite/e2e/app.spec.ts` — добавляй новые блоки `test()` или `test.describe()` по образцу существующих.



**Где хранится Chromium?**  
В папке `ms-playwright` в профиле пользователя. В репозиторий его не добавляют.

## Подробнее где хранится Chromium

Playwright ставит браузеры **не в проект**, а в общую папку по профилю пользователя:

| ОС        | Папка |
|-----------|-------|
| **Windows** | `%LOCALAPPDATA%\ms-playwright` → например `C:\Users\<имя>\AppData\Local\ms-playwright` |
| **Linux**   | `~/.cache/ms-playwright` |
| **macOS**   | `~/Library/Caches/ms-playwright` |

Внутри, как правило:
```
ms-playwright/
├── chromium-1208/           ← Chromium (версия зависит от Playwright)
│   └── chrome-win/
├── chromium-headless-shell-1208/
├── firefox-.../
└── webkit-.../
```

---

## Как проверить, установлен ли Chromium

### 1. Через команду Playwright

```powershell
cd my-app-vite
npx playwright install chromium --dry-run
```

- Если Chromium уже есть — будет сообщение вроде того, что он установлен.
- Если нет — Playwright предложит установить.

### 2. Проверить папку вручную (Windows)

```powershell
# Посмотреть содержимое папки Playwright
dir $env:LOCALAPPDATA\ms-playwright

# Список подпапок (если есть chromium-* — Chromium установлен)
Get-ChildItem $env:LOCALAPPDATA\ms-playwright -Directory
```

Если есть каталог `chromium-*` (например, `chromium-1208`) — Chromium установлен.

### 3. Запустить тест

```powershell
cd my-app-vite
npm run test:e2e
```

Если тесты стартуют и в логе есть запуск `[chromium]` без ошибок — Chromium установлен и используется.

Если нет:

```
browserType.launch: Executable doesn't exist at ...
```

значит нужно установить:

```powershell
npx playwright install chromium
```

---

## Почему не в репозиторий

- Браузер весит ~170 МБ и больше.
- Различается по ОС (Windows / Linux / macOS).
- Ставится один раз по `npx playwright install chromium` для всей системы.

В Git добавляют только код тестов и конфиг Playwright, сам браузер — нет.