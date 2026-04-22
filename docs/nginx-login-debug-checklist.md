## Проверка входа через `nginx`: шпаргалка

Короткая инструкция, если клиент снова пишет, что "не может войти".

Все команды ниже только смотрят состояние и логи, ничего не меняют, кроме отдельного блока "Исправление `proxy_pass`", который запускать только при необходимости.

### 1. Быстро проверить, куда `nginx` проксирует API

```bash
sudo grep -n "proxy_pass" /etc/nginx/sites-enabled/leadrecordwh.ru
sudo sed -n '1,220p' /etc/nginx/sites-enabled/leadrecordwh.ru
```

Нормально для этого проекта:

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8000/;
}
```

Если там стоит `http://localhost:8000/`, возможна плавающая ошибка через `::1`.

### 2. Проверить, на чём реально слушает backend

```bash
sudo ss -ltnp | grep 8000
```

Ожидаемо:

```text
LISTEN 0 2048 127.0.0.1:8000 ...
```

Если backend слушает только `127.0.0.1:8000`, а `nginx` проксирует на `localhost`, часть запросов может уходить в `::1:8000` и падать с `Connection refused`.

### 3. Смотреть access log в момент попытки входа

```bash
sudo tail -f /var/log/nginx/leadrecordwh.ru_access.log
```

Если известен IP клиента:

```bash
sudo tail -f /var/log/nginx/leadrecordwh.ru_access.log | grep "IP_КЛИЕНТА"
```

Успешный вход обычно выглядит так:

```text
POST /api/login HTTP/1.1" 200
GET /api/me HTTP/1.1" 200
GET /api/projects?limit=10000 HTTP/1.1" 200
```

### 4. Смотреть `nginx` error log в момент попытки входа

```bash
sudo tail -f /var/log/nginx/leadrecordwh.ru_error.log
```

На что смотреть:

- `connect() failed (111: Connection refused) while connecting to upstream`
- upstream вида `http://[::1]:8000/...`
- `limit_req`
- `access forbidden by rule`

Если после новой попытки входа в ошибках пусто, это хороший знак.

### 5. Смотреть backend-лог параллельно

```bash
tail -f /opt/MB_LK_leads/logs/app.log
```

Если запрос дошёл до backend, в логе появится что-то вроде:

```text
POST /login -> 200
GET /me -> 200
GET /projects -> 200
```

### 6. Как быстро понять, где ломается вход

#### Вариант A: в `nginx access log` нет `POST /api/login`

Значит проблема ещё на клиенте:

- браузер / WebView / Telegram
- сеть / VPN / Private Relay
- пользователь не дошёл до отправки формы

#### Вариант B: есть `POST /api/login 200`, потом идут `GET /api/me` и `GET /api/projects 200`

Значит вход успешный, сервер работает нормально.

#### Вариант C: в `nginx error log` появляется:

```text
connect() failed (111: Connection refused) while connecting to upstream
upstream: "http://[::1]:8000/..."
```

Значит `nginx` пытается идти в IPv6 localhost, а backend там не слушает.

### 7. Проверка firewall

```bash
sudo ufw status verbose
sudo iptables -S
sudo nft list ruleset
```

Для сайта должны быть открыты `80/tcp` и `443/tcp`.

Если backend работает за `nginx`, то `8000/tcp` может быть закрыт снаружи, это нормально.

### 8. Проверка логов по конкретному IP

```bash
sudo grep "IP_КЛИЕНТА" /var/log/nginx/leadrecordwh.ru_access.log
sudo grep "IP_КЛИЕНТА" /var/log/nginx/leadrecordwh.ru_error.log
grep "IP_КЛИЕНТА" /opt/MB_LK_leads/logs/app.log
```

Если в `app.log` IP напрямую не ищется, смотри по времени попытки входа.

### 9. Исправление `proxy_pass`, если снова увидим `::1`

Сначала бэкап:

```bash
sudo cp /etc/nginx/sites-enabled/leadrecordwh.ru /etc/nginx/sites-enabled/leadrecordwh.ru.bak-$(date +%F-%H%M%S)
```

Замена:

```bash
sudo sed -i 's|proxy_pass http://localhost:8000/;|proxy_pass http://127.0.0.1:8000/;|g' /etc/nginx/sites-enabled/leadrecordwh.ru
```

Проверка и reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
sudo grep -n "proxy_pass" /etc/nginx/sites-enabled/leadrecordwh.ru
```

### 10. Что просить у клиента

Лучше всего просить три вещи:

- точное время попытки входа
- какой браузер использовал
- что именно увидел после нажатия `Войти`

Этого обычно достаточно, чтобы быстро сопоставить попытку входа с логами.
