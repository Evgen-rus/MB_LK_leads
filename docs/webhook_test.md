# Инструкция: тестовый вебхук `webhook_test.py`

Этот файл описывает настройку и запуск отдельного мини‑сервиса для приёма тестовых лидов от провайдера.
Основное приложение не затрагивается.

## 1) Что это за сервис

Файл `webhook_test.py` — минимальный FastAPI‑сервер с одним POST‑endpoint.
Он принимает JSON или form‑data, пишет входящие данные в лог и возвращает `200 OK`.

Лог сохраняется в:
`logs/provider_webhook.log`

## 2) Где менять URL (секрет)

Секрет берётся из `.env`:
```
WEBHOOK_SECRET=<secret>
```

В `webhook_test.py` путь строится из переменной окружения:
```
@app.post(f"/api/provider-test/{WEBHOOK_SECRET}")
```

Если нужно поменять секрет — правим только `.env` и перезапускаем `uvicorn`.

## 3) Nginx: прокси на мини‑сервис

Конфиг nginx:
`/etc/nginx/sites-enabled/leadrecordwh.ru`

Добавляем location (точно с тем же секретом):
```
sudo nano /etc/nginx/sites-enabled/leadrecordwh.ru
```

```
location /api/provider-test/<secret> {
    proxy_pass http://127.0.0.1:8005/api/provider-test/<secret>;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Проверка и применение:
```
sudo nginx -t
sudo nginx -s reload
```

## 4) Как запускать

Из корня проекта:
```
uvicorn webhook_test:app --host 127.0.0.1 --port 8005
```

Пока терминал открыт — сервис работает.

## 5) URL для провайдера

В поле у провайдера указывается:
```
https://твой-домен.ru/api/provider-test/<secret>
```

## 6) Проверка, что всё работает

- В терминале `uvicorn` появится строка:
  `POST /api/provider-test/<secret> ... 200 OK`
- В логе появится запись в JSON:
  `logs/provider_webhook.log`

## 7) Остановка

В терминале, где запущен `uvicorn`, нажать:
`CTRL+C`

## 8) Безопасность (кратко)

Минимальная защита — секрет в URL.
Если появится список IP провайдера, можно дополнительно ограничить доступ в Nginx через `allow/deny`.
