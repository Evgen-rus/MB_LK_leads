# MB_LK_leads — Личный кабинет

Прототип личного кабинета с хедером, левым меню и таблицей проектов (поиск по названию/ID, фильтр по статусу).

## 🚀 Быстрый старт

### Предварительные требования
- Node.js 24.11.0+ (через NVM)
- Python 3.8+
- pip

### Запуск проекта

1. **Клонирование и установка зависимостей:**
```bash
git clone <repository-url>
cd MB_LK_leads

# Python зависимости
pip install -r requirements.txt

# Node.js зависимости
cd my-app-vite
npm install
cd ..
```

2. **Запуск бэкенда:**
```bash
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

3. **Запуск фронтенда (в новом терминале):**
```bash
cd my-app-vite
npm run dev
```

После запуска:
- **Фронтенд:** http://localhost:5173/
- **Бэкенд API:** http://localhost:8000/

## 📋 Детальная настройка

### Node.js установка/обновление

```bash
# Проверка версии
node --version  # v24.11.0

# Если версия старая, установка через NVM
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
source ~/.bashrc
nvm install 24
nvm use 24
```

### Тестирование API

```bash
# Проверка здоровья
curl http://localhost:8000/health

# Получение проектов
curl http://localhost:8000/projects

# Создание проекта (пример)
curl -X POST http://localhost:8000/projects \
  -H "Content-Type: application/json" \
  -d '{"name": "Новый проект", "status": "active"}'
```

## 🛠 Текущий стек

- **Frontend:**
  - React 19.2.0
  - TypeScript 5.9.3
  - Vite 7.1.12 + @vitejs/plugin-react 5.1.0
  - CSS (стили в `App.css`)

- **Backend:**
  - FastAPI (Python)
  - Uvicorn
  - SQLAlchemy (база данных)

- **DevOps:**
  - Node.js 24.11.0
  - npm 11.6.1
  - Git + .gitignore

## 📁 Структура проекта

```
MB_LK_leads/
├── backend/                 # Python FastAPI бэкенд
│   └── app/
│       ├── main.py         # Точка входа API
│       ├── models.py       # Модели данных
│       └── db.py           # Настройки БД
├── my-app-vite/            # React фронтенд
│   ├── src/
│   │   ├── components/     # React компоненты
│   │   │   ├── Header.tsx
│   │   │   ├── Sidebar.tsx
│   │   │   └── ProjectsTable.tsx
│   │   ├── data/
│   │   │   └── projects.ts # Мок-данные
│   │   ├── types/
│   │   │   └── project.ts  # TypeScript типы
│   │   ├── App.tsx         # Главный компонент
│   │   └── main.tsx        # Точка входа Vite
│   └── package.json
├── requirements.txt         # Python зависимости
└── README.md
```

## 🔧 Скрипты

### Фронтенд (в папке `my-app-vite`)
```bash
npm install      # Установка зависимостей
npm run dev      # Dev сервер (http://localhost:5173)
npm run build    # Сборка для продакшена
npm run preview  # Превью продакшен сборки
```

### Бэкенд (в корне проекта)
```bash
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

## 📝 API Endpoints

- `GET /health` - Проверка работоспособности
- `GET /projects` - Получение списка проектов
- `POST /projects` - Создание нового проекта
- `PUT /projects/{id}` - Обновление проекта
- `DELETE /projects/{id}` - Удаление проекта

## ⚠️ Примечания

- Проект перенесён с Create React App на Vite
- Данные временно хранятся в памяти (in-memory), планируется переход на PostgreSQL
- Конфигурация gitignore настроена для исключения системных и временных файлов