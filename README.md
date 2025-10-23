MB_LK_leads — Личный кабинет (React)

Короткое описание: прототип ЛК на React с хедером, левым меню и таблицей проектов. Есть поиск по названию/ID и фильтр по статусу.

Команды запуска (Windows PowerShell)
```bash
cd my-app
npm install  # первый запуск/после клонирования
npm start    # dev-сервер: http://localhost:3000
```

Сборка продакшн-версии
```bash
cd my-app
npm run build
```

Текущий стек
- React 18 + create-react-app 5.1.0 (react-scripts)
- JavaScript (ES2015+) без TypeScript
- CSS (простые стили в `App.css`)
- Webpack/Babel (из `react-scripts`, под капотом CRA)
- Node.js v22 + npm (локальная среда)
- Тестирование предустановлено (Jest + @testing-library/react), пока не используем
- Git + корневой `.gitignore`
- Данные временно мокируются в `my-app/src/data/projects.js` (бэкенда нет)

Важные файлы
- my-app/src/components/Header.js — верхняя панель
- my-app/src/components/Sidebar.js — левое меню
- my-app/src/components/ProjectsTable.js — таблица, поиск и фильтр
- my-app/src/data/projects.js — мок-данные
- my-app/src/App.js — сборка лейаута

Примечание
- Игнор системных/временных файлов настроен в корневом .gitignore (включая **/node_modules/).

