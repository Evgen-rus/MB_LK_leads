# Защита от скана `/.env` (пошагово)

Инструкция для Ubuntu-сервера с `nginx + uvicorn + systemd`.

## Еженедельный быстрый чек (4 команды)

Скопируй и выполни по порядку:

```bash
ss -ltnp | grep :8000
sudo ufw status verbose
curl -I https://YOUR_DOMAIN/.env
grep 'GET /.env' /opt/MB_LK_leads/logs/app.log | grep ' 200 ' || echo "OK: 200 нет"
```

Что проверяет:
- `8000` слушает только `127.0.0.1` (не `0.0.0.0`).
- firewall активен, `8000` закрыт снаружи.
- `/.env` через домен не отдается (`403/404`, не `200`).
- в логах нет выдачи `200` на `GET /.env`.

## Как работать с этим файлом

1. Выполняй команды блоками по порядку.
2. После каждого блока копируй вывод.
3. Присылай мне вывод, я проверю и скажу следующий шаг.

## 0) Подготовка (замени домен)

В командах ниже замени `YOUR_DOMAIN` на свой домен (например, `leadrecordwh.ru`).

## 1) Проверить, что backend слушает только локально

Команда:

```bash
ss -ltnp | grep :8000
```

Что делает:
- Показывает, на каком интерфейсе слушает порт `8000`.

Ожидаемо:
- Хорошо: `127.0.0.1:8000`
- Плохо: `0.0.0.0:8000` (доступен снаружи)

Что прислать мне:
- Полный вывод команды.

## 2) Проверить systemd unit backend-сервиса

Команда:

```bash
sudo systemctl cat lk-backend
```

Что делает:
- Показывает unit-файл сервиса, включая `ExecStart`.

Ожидаемо:
- В `ExecStart` должен быть `--host 127.0.0.1 --port 8000`.

Что прислать мне:
- Блок `[Service]` целиком.

## 3) Применить правку сервиса (если host не `127.0.0.1`)

Команды:

```bash
sudo systemctl daemon-reload
sudo systemctl restart lk-backend
sudo systemctl status lk-backend --no-pager -l
```

Что делают:
- Перечитывают unit-файлы, перезапускают backend, показывают статус.

Ожидаемо:
- `active (running)` без ошибок.

Что прислать мне:
- Вывод `status`.

## 4) Защитить dotfiles в nginx (`/.env`, `/.git`, ...)

Добавь в `server { ... }` этот блок:

```nginx
location ~ /\.(?!well-known).* {
    deny all;
    access_log off;
    log_not_found off;
}
```

После правки выполни:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Что делают:
- Проверяют синтаксис nginx и применяют конфиг без даунтайма.

Ожидаемо:
- `nginx: configuration file ... test is successful`

Что прислать мне:
- Вывод `nginx -t`.

## 5) Проверить доступ к `/.env` снаружи

Команда:

```bash
curl -I https://YOUR_DOMAIN/.env
```

Что делает:
- Проверяет HTTP-статус для попытки чтения `/.env`.

Ожидаемо:
- `403` или `404`
- Никогда не `200`

Что прислать мне:
- Полный вывод команды.

## 6) Настроить firewall (UFW)

Важно: сначала разрешаем SSH, потом включаем UFW.

Команды:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw deny 8000/tcp
sudo ufw enable
sudo ufw status verbose
```

Что делают:
- Оставляют снаружи только `22/80/443`, режут внешний доступ к `8000`.

Что прислать мне:
- Вывод `sudo ufw status verbose`.

## 7) Проверить логи на попытки доступа к `/.env`

Команды:

```bash
grep 'GET /.env' /opt/MB_LK_leads/logs/app.log | tail -n 50
grep 'GET /.env' /opt/MB_LK_leads/logs/app.log | grep ' 200 ' || echo "OK: 200 нет"
```

Что делают:
- Показывают последние попытки скана `/.env`.
- Проверяют, что не было `200` ответов.

Что прислать мне:
- Вывод обеих команд.

## Критический сигнал

Если для `/.env` когда-либо появился статус `200`:

1. Немедленно закрыть доступ (nginx + ufw).
2. Считать секреты скомпрометированными.
3. Ротация минимум:
- `AUTH_SECRET`
- `PROSTATS_TOKEN`
- `TELEGRAM_BOT_TOKEN`
- Google credentials (если были доступны публично).
