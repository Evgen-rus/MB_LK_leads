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

Важные файлы
- my-app/src/components/Header.js — верхняя панель
- my-app/src/components/Sidebar.js — левое меню
- my-app/src/components/ProjectsTable.js — таблица, поиск и фильтр
- my-app/src/data/projects.js — мок-данные
- my-app/src/App.js — сборка лейаута

Примечание
- Игнор системных/временных файлов настроен в корневом .gitignore (включая **/node_modules/).

