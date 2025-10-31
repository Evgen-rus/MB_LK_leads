Отлично. Самый простой способ на Ubuntu с доменом: Nginx раздаёт фронт (статик), проксирует /api на Uvicorn (FastAPI). Без ngrok.

### 0) Предподготовка (SSH на сервер)
- Обновить систему:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip nginx
```

### 1) Задеплоить проект на сервер
- Скопируй проект в, например, `/opt/lk`:
```bash
sudo mkdir -p /opt/lk && sudo chown $USER:$USER /opt/lk
# (далее залей файлы проекта в /opt/lk, например scp/rsync/git)
cd /opt/lk
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
- Создай .env с секретами (НЕ выкладывать публично):
```bash
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=ваш_токен
TELEGRAM_CHAT_ID=ваш_chat_id
DEBOUNCE_WINDOW_MINUTES=30
EOF
```

### 2) Поднять бэкенд (Uvicorn + systemd)
- Создай сервис:
```bash
sudo tee /etc/systemd/system/lk-backend.service > /dev/null << 'EOF'
[Unit]
Description=LK FastAPI backend
After=network.target

[Service]
User=%i
WorkingDirectory=/opt/lk
ExecStart=/opt/lk/venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
Restart=always
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
```
- Подставь своего пользователя (вместо %i не подставляется автоматически), например:
```bash
sudo sed -i "s/User=%i/User=$USER/" /etc/systemd/system/lk-backend.service
```
- Запусти:
```bash
sudo systemctl daemon-reload
sudo systemctl enable lk-backend
sudo systemctl start lk-backend
sudo systemctl status lk-backend --no-pager
```
- Проверка:
```bash
curl http://127.0.0.1:8000/health
```

Важно: сервис пишет логи в папку `/opt/lk/logs`. Убедись, что у пользователя есть права на запись (если что: `sudo chown -R $USER:$USER /opt/lk`).

### 3) Собрать фронт и выложить статику
Вариант A — собрать на сервере:
```bash
# Установить Node 18 LTS
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt install -y nodejs

cd /opt/lk/my-app-vite
# (по желанию) задать базовый URL API через префикс /api
echo 'VITE_API_BASE=/api' > .env.production
npm ci
npm run build
sudo mkdir -p /var/www/lk
sudo cp -r dist/* /var/www/lk/
```

Вариант B — собрать локально и скопировать на сервер:
- Локально: `npm ci && npm run build` в `my-app-vite`, затем папку `dist/` скопировать на сервер в `/var/www/lk`.

### 4) Nginx: статика + прокси /api -> бэкенд
- Конфиг:
```bash
sudo tee /etc/nginx/sites-available/lk.conf > /dev/null << 'EOF'
server {
    listen 80;
    server_name your-domain.tld;

    root /var/www/lk;
    index index.html;

    # SPA fallback
    location / {
        try_files $uri /index.html;
    }

    # API -> FastAPI
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 300;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/lk.conf /etc/nginx/sites-enabled/lk.conf
sudo nginx -t
sudo systemctl restart nginx
```

### 5) HTTPS (Let’s Encrypt)
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.tld
```
Проверь: https://your-domain.tld и https://your-domain.tld/api/health

### 6) Файрвол (если ufw включён)
```bash
sudo ufw allow 'Nginx Full'
```

### 7) Проверка, что всё живо
- Браузер: зайди на домен → страница фронта.
- В DevTools (Network): запросы к `/api/projects` должны быть 200.
- Терминал:
```bash
curl https://your-domain.tld/api/health
tail -f /opt/lk/logs/app.log
```
Создай/измени проект в UI → через 30 мин (или поставь временно 1 мин в .env и перезапусти сервис) придёт сообщение в Telegram.

### Примечания
- CORS: мы проксируем /api на тот же домен, поэтому CORS не мешает. Ничего трогать не нужно.
- Переменные: бэкенд читает `.env` в `/opt/lk`. Меняешь — `sudo systemctl restart lk-backend`.
- Обновления:
  - Бэкенд: `git pull` (или копирование), `pip install -r requirements.txt`, `sudo systemctl restart lk-backend`.
  - Фронт: `npm run build`, `sudo rm -rf /var/www/lk/* && sudo cp -r dist/* /var/www/lk/`, `sudo systemctl reload nginx`.




### Что будет после деплоя
- Фронт (`index.html` + JS) раздаёт Nginx по вашему домену.
- Бэкенд (FastAPI/Uvicorn) слушает ТОЛЬКО `127.0.0.1:8000` и доступен извне только через Nginx-прокси по пути `/api`.
- Мы ставим Basic Auth в Nginx на весь сайт: без логина/пароля страница даже не откроется.
- Плюс внутри самой страницы вход через форму: `POST /auth/login` ставит httpOnly-cookie `session`, и только с этой кукой API пускает на `/projects`.

Итого: «двойной барьер» — сначала Nginx (логин/пароль на сайт), потом наш логин внутри ЛК.

### Могут ли попасть без логина/пароля?
Если правильно настроить — нет. Реальные риски появляются только при ошибках в настройках или утечке секретов:

- Открыт порт бэкенда наружу (не должен быть: Uvicorn — только `127.0.0.1`).
- Нет HTTPS → перехват трафика в открытой сети.
- Слабые пароли (`AUTH_PASSWORD`, Basic Auth) или их утечка.
- Nginx сконфигурирован криво (например, `/api` открыт без Basic).
- Брутфорс логина. Решение: лимит запросов.
- Секреты в репозитории/.env попали в чужие руки.

### Что обязательно сделать на проде
- Настроить Nginx:
  - Раздавать фронт, проксировать `/api` → `http://127.0.0.1:8000/`.
  - Включить Basic Auth на весь `server {}`: `auth_basic` + `auth_basic_user_file`.
- HTTPS:
  - Выпустить сертификат (`certbot`) для домена.
- Ограничить доступ:
  - Uvicorn стартовать на `127.0.0.1`.
  - В `ufw` открыть только 80/443.
- Секреты и куки:
  - В `.env` задать длинные значения для `AUTH_PASSWORD` и `AUTH_SECRET` (30+ символов).
  - `COOKIE_SECURE=1` (обязательно при HTTPS).
  - `CORS_ORIGINS=https://ваш-домен`.
- Логи и модерация:
  - Следить за `logs/app.log`.
  - Телеграм уведомления уже работают.
- Брутфорс‑защита (по возможности):
  - В Nginx `limit_req` для `/auth/login`.
  - Или `fail2ban` по логам Nginx.

### Краткий чек‑лист «минимум, чтобы было безопасно»
- Бэкенд: только `127.0.0.1`, `.env` с сильными секретами, `COOKIE_SECURE=1`, `CORS_ORIGINS` = ваш домен.
- Nginx: Basic Auth на весь сайт, прокси `/api`, HTTPS.
- Файрвол: открыты 80/443, остальное закрыто.
- Пароли: длинные, не хранить в гите, менять при утечках.
- Логи: смотреть `app.log`, включить ротацию (у нас уже есть).

Если хотите, пришлю готовый `server`‑блок Nginx под ваш домен и команду `htpasswd` для создания пользователя, чтобы просто вставить и перезапустить.