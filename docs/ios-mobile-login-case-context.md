## Кейс: вход с iPhone по мобильной сети

Ниже собран фактический контекст по проблеме без выводов и без предложений по исправлению.

### 1. Симптом со слов клиента

- Клиент сообщает, что с iPhone на `iOS 18` вход не работает при использовании мобильного интернета.
- При подключении того же телефона к Wi-Fi вход выполняется.
- По дополнительной проверке:
  - на более новых версиях `iOS` вход выполняется;
  - на `Android` вход выполняется.

### 2. Домен и сервер

- Домен: `https://leadrecordwh.ru`
- Фронтенд раздаётся через `nginx`
- Backend работает локально на порту `8000`

Текущее состояние порта backend:

```text
LISTEN 0 2048 127.0.0.1:8000 0.0.0.0:* users:(("python",pid=328097,fd=10))
```

### 3. Текущий конфиг `nginx`

Актуальный фрагмент `/etc/nginx/sites-enabled/leadrecordwh.ru`:

```nginx
server {
    server_name leadrecordwh.ru www.leadrecordwh.ru;

    access_log /var/log/nginx/leadrecordwh.ru_access.log;
    error_log /var/log/nginx/leadrecordwh.ru_error.log;

    location / {
        root /opt/MB_LK_leads/my-app-vite/dist;
        index index.html index.htm;
        try_files $uri $uri/ /index.html;
    }

    location ~ /\.(?!well-known).* {
        deny all;
        access_log off;
        log_not_found off;
    }

    location /api/provider-test/5031138d-6743-3628-.... {
        proxy_pass http://127.0.0.1:8000/api/provider-test/5031138d-6743-3628-....;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    listen 443 ssl;
}
```

### 4. Историческое наблюдение по `nginx error log`

В `leadrecordwh.ru_error.log` ранее фиксировались ошибки с upstream на `::1`:

```text
connect() failed (111: Connection refused) while connecting to upstream
upstream: "http://[::1]:8000/..."
```

Примеры строк:

```text
2026/04/22 10:25:19 [error] ... request: "POST /api/login HTTP/1.1", upstream: "http://[::1]:8000/login", host: "leadrecordwh.ru"
2026/04/22 11:20:29 [error] ... request: "GET /api/projects?limit=10000 HTTP/1.1", upstream: "http://[::1]:8000/projects?limit=10000", host: "leadrecordwh.ru"
```

### 5. Изменение, которое уже было применено на сервере

На сервере была выполнена замена:

```bash
sudo sed -i 's|proxy_pass http://localhost:8000/;|proxy_pass http://127.0.0.1:8000/;|g' /etc/nginx/sites-enabled/leadrecordwh.ru
sudo nginx -t
sudo systemctl reload nginx
```

После этого в конфиге `location /api/` установлен `proxy_pass http://127.0.0.1:8000/;`.

### 6. Проверка доступа через firewall

На сервере активен `ufw`.

Ключевые правила:

```text
22/tcp ALLOW IN
80/tcp ALLOW IN
443/tcp ALLOW IN
8000/tcp DENY IN
```

### 7. Данные из `nginx access log` по iPhone-клиентам

#### Примеры успешных входов с iPhone

IP `128.204.66.240`:

```text
16/Apr/2026:06:16:43 +0000 "POST /api/login HTTP/1.1" 200
16/Apr/2026:06:16:43 +0000 "GET /api/projects?limit=10000 HTTP/1.1" 200
User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 18_6_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/147.0.7727.47 Mobile/15E148 Safari/604.1
```

IP `91.79.219.53`:

```text
16/Apr/2026:06:35:02 +0000 "POST /api/login HTTP/1.1" 200
16/Apr/2026:06:35:02 +0000 "GET /api/projects?limit=10000 HTTP/1.1" 200
16/Apr/2026:06:35:02 +0000 "GET /api/leads?... HTTP/1.1" 200
User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 18_6_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/147.0.7727.47 Mobile/15E148 Safari/604.1
```

IP `178.213.18.83`:

```text
16/Apr/2026:06:43:03 +0000 "POST /api/login HTTP/1.1" 200
16/Apr/2026:06:43:04 +0000 "GET /api/projects?limit=10000 HTTP/1.1" 200
16/Apr/2026:06:43:05 +0000 "GET /api/me HTTP/1.1" 200
User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 18_6_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/147.0.7727.47 Mobile/15E148 Safari/604.1
```

### 8. Конкретный кейс клиента в логах `22 Apr 2026`

IP: `37.192.138.114`

#### Открытие страницы логина

```text
22/Apr/2026:11:50:50 +0000 "GET /login HTTP/1.1" 200 316
22/Apr/2026:11:50:50 +0000 "GET /assets/index-Cz6KtdmR.css HTTP/1.1" 200
22/Apr/2026:11:50:50 +0000 "GET /assets/index-pV_ObnI7.js HTTP/1.1" 200
```

User-Agent:

```text
Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.3.1 Mobile/23D8133 Safari/604.1
```

Отдельно рядом зафиксирован запрос:

```text
22/Apr/2026:11:50:50 +0000 "GET /favicon.svg HTTP/1.1" 200
User-Agent: Telegram/32738 CFNetwork/3860.400.51 Darwin/25.3.0
```

#### Попытка входа

```text
22/Apr/2026:11:51:39 +0000 "POST /api/login HTTP/1.1" 200 213
22/Apr/2026:11:51:39 +0000 "GET /api/projects?limit=10000 HTTP/1.1" 200 22
22/Apr/2026:11:51:39 +0000 "GET /api/admin/users?includeAgents=true HTTP/1.1" 200 2736
22/Apr/2026:11:51:39 +0000 "GET /api/admin/changes/summary?actions=create%2Cupdate%2Cdelete%2Cblacklist_add%2Cblacklist_delete HTTP/1.1" 200 2959
22/Apr/2026:11:51:39 +0000 "GET /api/admin/clients/summary?fromDate=2026-04-22&toDate=2026-04-22 HTTP/1.1" 200 9661
```

#### Дальнейшая активность в кабинете

После этого с того же IP были зафиксированы успешные запросы:

```text
GET /api/admin/agents HTTP/1.1" 200
GET /api/admin/users HTTP/1.1" 200
GET /api/admin/reports?offset=0&limit=25 HTTP/1.1" 200
GET /api/admin/blacklist?offset=0&limit=50 HTTP/1.1" 200
POST /api/admin/clients/10/impersonate HTTP/1.1" 200
GET /api/me HTTP/1.1" 200
GET /api/balance HTTP/1.1" 200
GET /api/leads?... HTTP/1.1" 200
```

### 9. Локальные backend-логи

В `logs/app.log` ранее фиксировались строки вида:

```text
POST /login -> 200
GET /me -> 200
GET /projects -> 200
```

Для разных IP и разных мобильных user-agent.

### 10. Сводка фактов для дальнейшего исследования

- Домен: `leadrecordwh.ru`
- Frontend: `nginx`
- Backend: Python/FastAPI на `127.0.0.1:8000`
- В истории `nginx error log` были ошибки upstream на `http://[::1]:8000/...`
- В текущем конфиге `nginx` используется `proxy_pass http://127.0.0.1:8000/;`
- По словам клиента:
  - на `iOS 18` через мобильный интернет вход не работает;
  - на `Wi-Fi` вход работает;
  - на более новых `iOS` вход работает;
  - на `Android` вход работает.
- В access log есть успешные входы и дальнейшие успешные API-запросы с iPhone.
- Для IP `37.192.138.114` 22 апреля зафиксирован успешный `POST /api/login 200` и последующая работа внутри кабинета.
