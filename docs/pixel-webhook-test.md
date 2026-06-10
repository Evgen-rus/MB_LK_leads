# Инструкция: тестовый вебхук Пикселя

Этот мини-сервис нужен только для первого этапа: принять реальные payload Пикселя, записать их в отдельный лог и вернуть `200 OK`.
Основное приложение, БД, баланс и идентификации он не трогает.

## 1) Endpoint

```
POST /api/pixel-webhook/{PIXEL_WEBHOOK_SECRET}
```

Секрет берётся из `.env`:

```env
PIXEL_WEBHOOK_SECRET=replace-with-long-random-secret
```

Если `PIXEL_WEBHOOK_SECRET` не задан, сервис не стартует.

## 2) Лог

Входящие запросы пишутся в UTF-8:

```
logs/pixel_webhook.log
```

В каждую запись попадает JSON со сводкой `vid`, `site`, `page`, количеством телефонов, `ip`, `device`, `browser`, `platform`, а также полный payload и headers.

## 3) Запуск

Из корня проекта:

```bash
uvicorn pixel_webhook_test:app --host 127.0.0.1 --port 8006
```

Пока процесс запущен, сервис принимает запросы.

## 4) Nginx

Пример location для прокси:

```nginx
location /api/pixel-webhook/<secret> {
    proxy_pass http://127.0.0.1:8006/api/pixel-webhook/<secret>;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Проверка и применение:

```bash
sudo nginx -t
sudo nginx -s reload
```

## 5) URL для источника

В настройках источника указывается:

```
https://твой-домен.ru/api/pixel-webhook/<secret>
```

## 6) Проверка

Тестовый JSON:

```bash
curl -X POST "https://твой-домен.ru/api/pixel-webhook/<secret>" \
  -H "Content-Type: application/json" \
  -d '{"vid":"1689220848","site":"realestatesale.ae","phones":["79209160716"],"device":"Mobile Phone","browser":"Chrome","platform":"Android"}'
```

Ожидаемый ответ:

```json
{"ok":true}
```

После запроса проверь:

```
logs/pixel_webhook.log
```

## 7) Важно

На этом этапе сервис только собирает фактический формат данных. Он не создаёт идентификации, не списывает остаток, не ищет проект и не пишет в БД.
