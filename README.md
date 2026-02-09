# MB_LK_leads — Личный кабинет

## 🚀 Быстрый старт

### Предварительные требования
- Node.js 24.11.0+ (через NVM)
- Python 3.8+
- PostgreSQL 18 (настройка см. `docs/PostgreSQL.md`)

### Запуск проекта

1. **Клонирование и установка зависимостей:**
```bash
git clone <repository-url>
cd MB_LK_leads
```

# Python зависимости
```bash
python -m venv venv
venv\Scripts\activate   # Windows
pip install -r requirements.txt

# Linux/macOS
python -m venv venv
source venv/bin/activate
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
> Перед запуском убедитесь, что в `.env` задан `DATABASE_URL` для Postgres.

3. **Запуск фронтенда (в новом терминале):**
```bash
cd my-app-vite
npm run dev
```

4. **Проверка кода фронтенда линтером (опционально, но рекомендуется):**
```bash
cd my-app-vite
npm run lint
```
Проверяет код на ошибки и соответствие стандартам качества.

После запуска:
- **Фронтенд:** http://localhost:5173/
- **Бэкенд API:** http://localhost:8000/


## Правила портов в Linux/Windows:

**Диапазон портов**: 0–65535 (всего 65536 портов)

**Привилегированные порты (0–1023)**:
- Требуют прав root/admin (sudo)
- Примеры: 80 (HTTP), 443 (HTTPS), 22 (SSH), 21 (FTP)
- Без root uvicorn выдаст ошибку: `Permission denied`

**Пользовательские порты (1024–65535)**:
- Можно использовать без root
- Рекомендуется для разработки: 8000+, 3000+, 5000+
- Ваши примеры (8001, 7999) — нормально

## Примеры использования:

```bash
# Хорошо (пользовательские порты)
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8001
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 7999
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 3000

# Плохо (привилегированные, нужен root)
sudo uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 80

# Может быть занято (проверьте)
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8080  # Часто занят
```

## Практические советы:

1. **Проверьте занятость порта** перед запуском:
   ```bash
   netstat -tlnp | grep :8001  # Linux
   netstat -ano | findstr :8001  # Windows
   ```

2. **Если порт занят** — uvicorn покажет ошибку `[Errno 98] Address already in use`

3. **Для разработки**: используйте 8000–8999, это стандартно

4. **Для продакшена**: обычно 80/443 (через nginx прокси) или 8000–9999



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
  - Vite 7.1.12 + @vitejs/plugin-react 5.0.4
  - ESLint 9.36.0 + плагины для React
  - CSS (стили в `App.css`)

- **Backend:**
  - FastAPI 0.120.2 (Python веб-фреймворк)
  - Uvicorn 0.38.0 (ASGI сервер)
  - SQLAlchemy 2.0.44 (ORM для базы данных)
  - Pydantic 2.12.3 (валидация данных)
  - PyJWT 2.9.0 (работа с JWT токенами)
  - bcrypt 4.2.0 (хэширование паролей)
  - python-dotenv 1.2.1 (переменные окружения)
  - requests 2.32.5 (HTTP запросы)

- **Интеграции:**
  - Google Sheets API (google-api-python-client 2.151.0)
  - Google Auth 2.43.0 (аутентификация Google)
  - Telegram Bot API
  - Excel файлы (openpyxl 3.1.5)
  - tzdata 2024.1 (часовые пояса)

- **DevOps:**
  - Node.js 24.12.0
  - npm 11.7.0
  - Python 3.8+
  - PostgreSQL (см. `docs/PostgreSQL.md`)
  - Git + .gitignore

## 📁 Структура проекта

```
MB_LK_leads/
├── backend/                          # Python FastAPI бэкенд
│   └── app/
│       ├── __init__.py              # Инициализация пакета
│       ├── main.py                  # Точка входа API (FastAPI приложение)
│       ├── models.py                # SQLAlchemy модели данных
│       ├── db.py                    # Настройки базы данных
│       ├── crud.py                  # CRUD операции с БД
│       ├── schemas.py               # Pydantic схемы для API
│       ├── auth.py                  # Аутентификация и авторизация
│       ├── telegram.py              # Интеграция с Telegram
│       ├── notify_worker.py         # Фоновые уведомления
│       ├── logging_setup.py         # Настройка логирования
│       └── time_utils.py            # Утилиты для работы со временем
├── my-app-vite/                     # React фронтенд (Vite)
│   ├── src/
│   │   ├── components/              # React компоненты UI
│   │   │   ├── Sidebar.tsx          # Левое меню навигации
│   │   │   ├── ProjectsTable.tsx    # Таблица проектов
│   │   │   ├── LeadsTable.tsx       # Таблица лидов
│   │   │   ├── AdminClientsScreen.tsx # Админ-панель клиентов
│   │   │   ├── Login.tsx            # Форма авторизации
│   │   │   └── ... (30+ компонентов)
│   │   ├── types/                   # TypeScript типы
│   │   │   └── project.ts           # Типы для проектов
│   │   ├── utils/                   # Утилиты фронтенда
│   │   │   ├── jwt.ts              # Работа с JWT токенами
│   │   │   ├── phones.ts           # Обработка телефонов
│   │   │   └── impersonation.ts    # Имперсонация пользователей
│   │   ├── data/
│   │   │   └── regions.ts          # Данные регионов
│   │   ├── api.ts                  # HTTP-клиент для API
│   │   ├── App.tsx                 # Главный компонент приложения
│   │   ├── main.tsx                # Точка входа Vite
│   │   ├── index.css               # Глобальные стили
│   │   └── logger.ts               # Логирование на фронте
│   ├── public/                     # Статические файлы
│   ├── package.json                # Зависимости Node.js
│   ├── vite.config.ts              # Конфигурация Vite
│   ├── tsconfig.json               # Конфигурация TypeScript
│   └── README.md                   # Документация фронтенда
├── venv/                           # Python виртуальное окружение
├── credentials/                    # Ключи и credentials
│   └── sheets-data-bot-b8f4cc6634fc.json  # Google Sheets API ключ
├── logs/                          # Логи приложения
│   └── app.log                    # Основной лог-файл            
├── requirements.txt               # Python зависимости
├── .gitignore                     # Исключаемые из Git файлы
├── tool_*.py                      # CLI утилиты для управления
│   ├── tool_user_tools.py         # Управление пользователями
│   ├── tool_db_tools.py           # Работа с БД
│   ├── tool_inspect_db.py         # Инспекция БД
│   ├── tool_map_projects.py       # Маппинг проектов
│   └── tool_create_unmapped_project.py # Создание fallback-проекта
├── task.md                        # Задачи проекта
├── SWAP_SETUP.md                  # Настройка swap-файла
├── bitrix_widget_restore.md       # Восстановление Bitrix виджета
├── защита_от_перебора.md          # Защита от перебора паролей
└── README.md                      # Эта документация
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

### Управление пользователями (CLI)

- Пользователи для первого запуска создаются из `.env` (пары `USER_1_LOGIN` / `USER_1_PASSWORD` и т.д.) **только если БД пустая**.
- Для дальнейшего управления логинами/паролями используйте скрипт `tool_user_tools.py` в корне проекта:

```bash
venv\Scripts\activate   # Windows

# Показать пользователей
python tool_user_tools.py list

# Создать нового пользователя
python tool_user_tools.py create --login <логин>

# Сменить пароль существующему пользователю
python tool_user_tools.py set-password --login <логин>
```

При смене пароля скрипт дополнительно записывает логин и новый пароль в локальный файл `users.txt` (он добавлен в `.gitignore` и не попадает в репозиторий).

### CLI утилиты для работы с проектом

```bash
# Инспекция базы данных
python tool_inspect_db.py                    # Просмотр всех проектов и provider_leads

```

## 🔌 Вебхук провайдера (provider leads)

### Назначение
Принимает лиды от провайдера и сохраняет их в новую таблицу `provider_leads`.

### Эндпоинт
- `POST /api/provider-test/{WEBHOOK_SECRET}`
- Секрет берётся из `.env` (`WEBHOOK_SECRET`)

### Логирование
- Лог: `logs/provider_webhook.log`
- Ротация: раз в сутки, хранение 30 дней

### Таблица `provider_leads`
Основные поля:
- `vid` — уникальный внешний ID (дедупликация по нему)
- `phone` — строка (если несколько телефонов, объединяем через запятую)
- `phones_raw` — JSON‑массив исходных телефонов
- `project_name` — название проекта из `page`
- `prov_created_at` — время из `time` (MSK)
- `prov_chanel` — `B1/B2/B3/B4` из начала `page`
- `prov_source` — часть после второго `_` в `page` (если есть)
- `subdomain` — если пришло в payload
- `imported_at` — время записи в БД (MSK)
- `project_id` — сопоставляется по `projects.name == project_name` (может быть `NULL`)

### nginx (прокси)
Пример прокси для вебхука:
```nginx
location /api/provider-test/<secret> {
    proxy_pass http://127.0.0.1:8000/api/provider-test/<secret>;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

### Экспорт `provider_leads` в Google Sheets (cron)
Скрипт: `tool_export_provider_leads.py`
в env прописываем

GOOGLE_SHEET_ID=id гугл таблицы куда из БД выгружать лиды
GOOGLE_SHEET_NAME=имя листа
# Фильтр “данные из бд в выгрузку в гугл таблицу за последние N дней”
LEADS_EXPORT_LOOKBACK_DAYS=3

Cron‑расписание (каждый час в 08:05 и 08:20 по МСК до 17:20):
```
CRON_TZ=Europe/Moscow
5 8-17 * * * cd /opt/MB_LK_leads && /opt/MB_LK_leads/venv/bin/python tool_export_provider_leads.py
20 8-17 * * * cd /opt/MB_LK_leads && /opt/MB_LK_leads/venv/bin/python tool_export_provider_leads.py
```

Проверка:
```
crontab -l
```

## 📝 API Endpoints

- `GET /health` - Проверка работоспособности
- `GET /projects` - Получение списка проектов
- `POST /projects` - Создание нового проекта
- `PATCH /projects/{id}` - Обновление проекта
- `DELETE /projects/{id}` - Удаление проекта

## 👑 Админская версия

**Доступ:** Пользователь с `id=1` автоматически получает права администратора.

**Возможности:**
- **Вкладка "Клиенты"** — просмотр и управление проектами всех клиентов
- **Управление статусом отгрузки** — изменение `delivery_status` (Активна/На модерации/Отключена) прямо в таблице через dropdown
- **Расширенный просмотр** — во всех разделах (Идентификации, Отчёты, Черный список) добавлен столбец "Клиент" с логином и id
- **Фильтрация по клиенту** — во всех админских таблицах доступна фильтрация по пользователю

**Админские эндпоинты:**
- `GET /admin/users` - Список всех пользователей
- `GET /admin/projects` - Все проекты всех клиентов
- `PATCH /admin/projects/{id}` - Редактирование проекта (включая delivery_status)
- `GET /admin/leads` - Все лиды с информацией о клиентах
- `GET /admin/blacklist` - Весь чёрный список
- `GET /admin/reports` - Все отчёты


---

## 🧭 Краткая инструкция деплоя и привязки домена (пример leadrecordwh.ru)

Шпаргалка для развёртывания на новом сервере (Ubuntu), привязки домена и запуска как сервиса.

1) DNS (в панели регистратора)

```
Тип: A
Имя: @
Значение: IP_СЕРВЕРА (пример: 82.147.71.51)
TTL: 600
```

Проверка: whatsmydns или `nslookup leadrecordwh.ru` — должен резолвиться в IP сервера.

2) Сервер: подготовка окружения

```bash
ssh root@IP_СЕРВЕРА
apt update && apt install -y git python3 python3-venv python3-pip nginx

# Node.js через NVM (для сборки фронтенда)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
source ~/.bashrc
nvm install 24 && nvm use 24
```

3) Деплой проекта в /opt/MB_LK_leads

```bash
cd /opt
git clone <repository-url> MB_LK_leads
cd MB_LK_leads

# Python зависимости
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Фронтенд: сборка прод-версии
cd my-app-vite
echo 'VITE_API_BASE=/api' > .env.production
npm install
npm run build
```

4) Nginx (раздача фронта + прокси на API)

Создаём файл конфига:
```bash
sudo nano /etc/nginx/sites-available/leadrecordwh.ru
```

Вставляем содержимое и сохраняем (Ctrl+O → Enter → Ctrl+X):
```nginx
server {
    listen 80;
    server_name leadrecordwh.ru www.leadrecordwh.ru;

    access_log /var/log/nginx/leadrecordwh.ru_access.log;
    error_log  /var/log/nginx/leadrecordwh.ru_error.log;

    location / {
        root /opt/MB_LK_leads/my-app-vite/dist;
        index index.html index.htm;
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Включаем сайт и перезапускаем Nginx:
```bash
sudo ln -sf /etc/nginx/sites-available/leadrecordwh.ru /etc/nginx/sites-enabled/leadrecordwh.ru
sudo nginx -t
sudo systemctl reload nginx
```

5) Uvicorn как сервис (systemd)

Создаём unit-файл сервиса:
```bash
sudo nano /etc/systemd/system/lk-backend.service
```

Вставляем содержимое и сохраняем:
```ini
[Unit]
Description=FastAPI backend (LK)
After=network.target

[Service]
User=root
WorkingDirectory=/opt/MB_LK_leads
ExecStart=/opt/MB_LK_leads/venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Активируем и запускаем сервис:
```bash
sudo systemctl daemon-reload
sudo systemctl enable lk-backend
sudo systemctl start lk-backend
sudo systemctl status lk-backend --no-pager
```

6) Проверка

```bash
curl http://leadrecordwh.ru/            # фронт
curl http://leadrecordwh.ru/api/health  # API через домен
```

7) .env (минимум для прод)

```
# Разрешённые источники для CORS
CORS_ORIGINS=http://leadrecordwh.ru,https://leadrecordwh.ru,http://localhost:5173

# Простейшая аутентификация
AUTH_USER=<логин>
AUTH_PASSWORD=<пароль>
AUTH_SECRET=<случайная_строка>

# Для HTTPS позже переключите на 1
COOKIE_SECURE=0
```

8) (Опционально позже) HTTPS через Let's Encrypt

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d leadrecordwh.ru -d www.leadrecordwh.ru
# затем в .env: COOKIE_SECURE=1 и перезапуск сервиса
```

Примечание: для развёртывания на другом сервере поменяйте IP и домен, пути оставьте `/opt/MB_LK_leads` как в примере — так проще переносить.

Отлично, проект запущен. Коротко, как обновлять его через GitHub и что пересобирать.

### Базовый цикл обновления

1) Локально (на вашем ПК)
- Вносите изменения → коммит → push в GitHub:
```bash
git add .
git commit -m "feature: описание"
git push origin main
```

2) На сервере (обновление кода)
```bash
ssh root@82.147.71.51
cd /opt/MB_LK_leads
git pull
```

3) Если менялся бэкенд (Python)
- Обновить зависимости и перезапустить сервис:
```bash
source venv/bin/activate
pip install -r requirements.txt
systemctl restart lk-backend
journalctl -u lk-backend -n 50 --no-pager
```

4) Если менялся фронтенд (React/Vite)
- Пересобрать статические файлы:
```bash
# убедиться, что активен Node 24 (если nvm)
source ~/.bashrc && nvm use 24

cd /opt/MB_LK_leads/my-app-vite
npm ci   # или npm install
npm run build
```
Nginx отдаёт `dist/` автоматически — перезапуск Nginx не нужен (только если меняли конфиг).

5) Проверка
```bash
curl http://leadrecordwh.ru/api/health
# в браузере: http://leadrecordwh.ru/ (жёсткое обновление: Ctrl+F5)
```

### Быстрые сценарии

- Только фронтенд менялся:
```bash
cd /opt/MB_LK_leads && git pull --rebase
source ~/.bashrc && nvm use 24
cd my-app-vite && npm ci && npm run build
```

- Только бэкенд менялся:
```bash
cd /opt/MB_LK_leads && git pull --rebase
source venv/bin/activate && pip install -r requirements.txt
systemctl restart lk-backend
```

### Важные примечания
- `.env` и `app.db` не в Git — они остаются на сервере как есть (это правильно).
- Переменная `VITE_API_BASE=/api` уже задана в `my-app-vite/.env.production` — при каждой сборке учитывается автоматически.
- Если меняли Nginx-конфиг, применяйте:
```bash
nginx -t && systemctl reload nginx
```

 