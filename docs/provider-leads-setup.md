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
# Скользящее окно: в очередь выгрузки попадают лиды, импортированные за последние N дней.
# Работает как ограничитель против повторной выгрузки истории после чистки таблицы.
LEADS_EXPORT_LOOKBACK_DAYS=5

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

---