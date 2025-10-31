MB_LK_leads — Личный кабинет (React + Vite + TypeScript)

Короткое описание: прототип ЛК с хедером, левым меню и таблицей проектов (поиск по названию/ID, фильтр по статусу).

Команды запуска (Windows PowerShell)

Запустить бэкенд (из корня проекта). Для быстрой проверки телеграма предлагаю временно окно 1 минуту
```bash
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000        
```

Запуск фронтенда:
```bash
Invoke-WebRequest http://127.0.0.1:8000/health | Select-Object -Expand Content
Invoke-WebRequest http://127.0.0.1:8000/projects | Select-Object -Expand Content
```

Проверка API
```bash
cd my-app-vite
npm run dev
```

Если нужно быстро дернуть API без фронта:
```bash
# Список проектов
Invoke-WebRequest http://localhost:8000/projects

# Пример создания (тело в JSON файле create.json)
Invoke-WebRequest -Uri http://localhost:8000/projects -Method POST -ContentType "application/json" -InFile .\create.json
```

Первый запуск дописать
```bash
cd my-app-vite
npm install  # первый запуск/после клонирования
npm run dev  # dev-сервер: http://localhost:5173
```

Сборка и превью продакшн-версии
```bash
cd my-app-vite
npm run build
npm run preview  # локальный предпросмотр сборки
```

Текущий стек
- React 19.2.0
- TypeScript 5.9.3
- Vite 7.1.12 + @vitejs/plugin-react 5.1.0 (dev — esbuild 0.25.11, prod — Rollup 4.52.5)
- CSS (простые стили в `App.css`)
- Node.js 24.10.0
- npm 11.6.1
- Git + корневой `.gitignore`
- Данные временно мокируются в `my-app-vite/src/data/projects.ts` (бэкенда нет)

Важные файлы
- my-app-vite/src/components/Header.tsx — верхняя панель
- my-app-vite/src/components/Sidebar.tsx — левое меню
- my-app-vite/src/components/ProjectsTable.tsx — таблица, поиск и фильтр
- my-app-vite/src/data/projects.ts — мок-данные
- my-app-vite/src/types/project.ts — типы данных
- my-app-vite/src/App.tsx — сборка лейаута
- my-app-vite/src/main.tsx — точка входа Vite

Примечание
- Старый CRA-проект перенесён на Vite. Игнор системных/временных файлов настроен в корневом .gitignore (включая **/node_modules/).

