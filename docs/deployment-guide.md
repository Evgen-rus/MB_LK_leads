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

cd /opt
git clone <repository-url> MB_LK_leads
cd MB_LK_leads

# Python зависимости
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Фронтенд: сборка прод-версии
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
sudo systemctl daemon-reload
sudo systemctl enable lk-backend
sudo systemctl start lk-backend
sudo systemctl status lk-backend --no-pager
6) Проверка
curl http://leadrecordwh.ru/            # фронт
curl http://leadrecordwh.ru/api/health  # API через домен
```

7) .env (минимум для прод)

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
apt install -y certbot python3-certbot-nginx
certbot --nginx -d leadrecordwh.ru -d www.leadrecordwh.ru
# затем в .env: COOKIE_SECURE=1 и перезапуск сервиса
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