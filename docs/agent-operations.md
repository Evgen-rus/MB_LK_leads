# LeadRecord: работа агента через SSH

Предпочтительный operational interface: `agent → SSH → lkctl → localhost
/agent/v1/* → существующий LeadRecord backend`. CLI не импортирует приложение,
не открывает БД и не запускает worker. MCP и браузер не нужны.

## Доступ и контракт

Backend читает `LK_AGENT_READ_TOKEN` (scope `read`) и
`LK_AGENT_COMPUTE_TOKEN` (scopes `read`, `compute`). Можно настроить только read.
Токены должны быть различными, случайными и храниться вне Git. Без токенов
или при совпадении токенов API закрыт. Администраторский пароль/JWT не подходят.
Machine token даёт доступ ко всем клиентам; ограничения на одного клиента
в первой версии отсутствуют.

CLI получает разрешённый токен через `LK_AGENT_TOKEN`, URL через
`LK_AGENT_URL` (по умолчанию `http://127.0.0.1:8000`). Разрешён только localhost
HTTP; redirects и proxy отключены. Передавать токен аргументом командной строки
или в URL нельзя. Не выводите environment, не включайте shell tracing.

`lkctl capabilities` возвращает версию `1`, операции, параметры и доступность
по scopes. Read использует GET `/agent/v1/{capability}` с query-параметрами;
compute — POST `/agent/v1/analytics.run` с JSON. Ответы:

```json
{"ok":true,"action":"client.show","data":{"client_id":17},"request_id":"…"}
```

```json
{"ok":false,"action":"client.show","error":{"code":"CLIENT_NOT_FOUND","message":"Клиент не найден"},"request_id":"…"}
```

JSON пишется в stdout; `--help` выводит обычную справку. Exit codes: `0` успех,
`1` ошибка сервиса/доступа, `2` неверные аргументы или `needs_input`.
Имена полей агрегатов discovery и API — контракт версии 1; безопасные проекции
данных используют поля существующих моделей, указанные в JSON-ответе.

## Команды

```bash
lkctl capabilities
lkctl overview
lkctl overview --from 2026-09-01 --to 2026-09-30
lkctl clients list --limit 50 --offset 0
lkctl clients find "Рио-Люкс"
lkctl client show --id 17
lkctl projects list --client 17
lkctl project show --id 123
lkctl project stats --id 123 --from 2026-09-01 --to 2026-09-30
lkctl leads stats --client 17 --from 2026-09-01 --to 2026-09-30
lkctl analytics groups --client 17
lkctl analytics history --group 8
lkctl analytics result --group 8 --id 42
lkctl analytics result --group 8 --run RUN_ID
lkctl analytics run --group 8 --period 2026-09-01:2026-09-30
lkctl analytics run --group 8 --run RUN_ID
```

Даты включают последний день, используют `SHEETS_TZ`; для read по умолчанию
сегодня. Максимальный диапазон 366 дней, страницы — 50 по умолчанию, максимум
200. Поиск возвращает варианты и не выбирает неоднозначного клиента за агента.
История аналитики следует порядку штатного реестра выгрузок.
`analytics.result` возвращает агрегаты отчёта либо состояние запуска и job;
Excel и сырые строки через этот API не выдаются.

## Analytics compute и needs_input

Для нового `--group --period` ответ `needs_input / PREPARATION_REQUIRED`:
текущий pipeline требует клиентский XLSX/Google, проверку колонок и
сопоставление. Подготовьте их штатно в разделе «Аналитика», сохраните проверенный
mapping и статусные правила. Затем передайте `run_id` из ответа подготовки.
Сама команда `run` не читает Google и не угадывает источник/вкладку/колонки;
для настроенной группы используйте `plan` и `prepare`, описанные ниже.
В текущем UI mapping сохраняется при подтверждённом запуске анализа; просмотр
setup сам по себе его не сохраняет. Поэтому новая группа без такого mapping
сначала требует штатного ручного анализа. Последующие подготовленные снимки
могут использовать сохранённые настройки, если проверка колонок проходит.

Уже сопоставленный (`matched`) запуск с сохранённым mapping и известными
статусами ставится в штатную очередь backend. Используются зафиксированные
периоды и снимки run. Mapping и правила не изменяются. HTTP-ответ возвращает
job, ожидание не блокирует CLI; опрашивайте `analytics result --group … --run …`.
Запрос активного run возвращает существующую job. Завершённый run требует нового
снимка (`NEW_RUN_REQUIRED`), чтобы повтор случайно не запустил расчёт заново.
Прерванный/ошибочный run требует проверки через UI, автоматического retry нет.

`needs_input` — остановка compute, а не успешный анализ. `UNKNOWN_STATUSES`
возвращает неизвестные значения; распределить их должен человек в штатной
аналитике или явной командой `confirm-statuses`. `MAPPING_REQUIRED` / `MATCH_REQUIRED` требуют проверки колонок или
завершения сопоставления. Не назначайте категории эвристически. Сохраните
request id для диагностики. Произвольное редактирование правил через Agent API не принимается.

Запрещены создание/удаление/изменение проектов, пауза/возобновление, лимиты,
клиенты/владельцы, тарифы/балансы, импорт лидов, произвольное изменение правил аналитики,
удаление групп/отчётов и изменяющие вызовы Prostats. Сырые лиды недоступны.
Operational-задачи не выполняются прямым SQL или отдельным Python worker.

## Повторный анализ настроенной группы

Первоначальный анализ и выбор таблицы/колонок выполняются в штатном UI.
`analytics.plan` показывает сохранённые настройки и готовность повторного
запуска без чтения Google. LR-маркеры берутся точно из квадратных скобок имени
группы и её проектов; новые проекты ищутся только у того же клиента.
Колонки не восстанавливаются эвристически. Если настройки неполны, оператор
получает `needs_input` и перечень недостающего.

```bash
lkctl analytics plan --group 8 --period 2026-09-28:2026-10-02
lkctl-compute analytics prepare --group 8 --period 2026-09-28:2026-10-02
# Только после подтверждения человеком конкретных новых ID:
lkctl-compute analytics prepare --group 8 --period 2026-09-28:2026-10-02 --confirm-projects 123,124
lkctl analytics result --group 8 --run RUN_ID
lkctl-compute analytics run --group 8 --run RUN_ID
# Только после явного назначения неизвестного статуса человеком:
lkctl-compute analytics confirm-statuses --group 8 --run RUN_ID --assign 'Новый статус=Рабочий потенциал'
lkctl-compute analytics run --group 8 --run RUN_ID
lkctl-compute analytics download --group 8 --id EXPORT_ID --output /absolute/new/report.xlsx
```

`prepare` обновляет Google-таблицу по сохранённой ссылке, выбирает сохранённую
вкладку, проверяет колонки и создаёт новый снимок/запуск в той же очереди match.
Не повторяйте prepare, пока ожидаете job: это новый снимок, а не polling.
После завершения match вызывается run; после завершения analyze скачивается
конкретный export. Новые статусы получают только явно подтверждённые exact
правила из разрешённых категорий. Обычное чтение JSON не даёт сырых лидов.

Скачивание требует **compute** scope: штатный Excel может содержать строки
лидов. При успехе HTTP отдаёт бинарный XLSX вместо JSON; ошибки сохраняют
обычный envelope. CLI пишет атомарно в новый локальный путь и возвращает JSON
с путём, размером и sha256. Существующий файл не перезаписывается. Путь не
принимается сервером, не выводятся токены или содержимое строк.

Файл после SSH-команды находится на VPS; чтобы передать его локальному Bridge,
нужно сначала перенести конкретный файл. Отправку в Telegram выполняет Bridge,
не LeadRecord backend. Операторский skill: `.agents/skills/leadrecord-analytics/SKILL.md`.
Два потока Рио-Люкса требуют отдельно настроенных групп/выбранных вкладок;
первый сценарий реализуется на одной существующей вкладке Эпкары.

## Отдельная настройка на VPS

Этот runbook не означает выполненный deploy или перезапуск production.

1. При отдельно согласованном deploy добавьте токены в защищённый environment
   работающего backend, а разрешённый токен — в environment SSH-оператора.
   Не копируйте всю backend `.env` агенту: она содержит чужие секреты.
   Права файла секрета: только владельцу (`chmod 600`); запрещены shell history
   и вывод значений токенов в диагностику.
2. Используйте один backend-процесс согласно ограничениям аналитики.
   Проверьте фактический localhost-порт; задайте его в `LK_AGENT_URL`.
   Доступ к порту backend ограничьте loopback/firewall. IP-check не заменяет auth.
3. В существующий server block публичного Nginx добавьте оба location ниже,
   чтобы более общий `proxy_pass` не публиковал Agent API. Конфигурация Nginx
   в этом репозитории не предполагается. Если proxy переписывает префикс URL,
   закройте также фактический внешний путь, который отображается в `/agent/v1`.

   ```nginx
   location = /agent/v1 { return 404; }
   location ^~ /agent/v1/ { return 404; }
   ```

   Перед применением проверьте `nginx -t`; reload и внешнюю проверку выполняйте
   только в рамках отдельного разрешения. Backend auth остаётся обязательной.
4. Root wrapper `lkctl` запускает Python из `venv`; при другом окружении задайте
   `LKCTL_PYTHON`. Установите wrapper в PATH либо вызывайте `./lkctl` из checkout.
   На Linux задайте executable bit (`chmod +x lkctl`). Альтернатива из корня:
   `venv/bin/python -m backend.app.agent_cli capabilities`.
5. Read smoke: `lkctl capabilities`, затем `lkctl overview`. Compute проверяйте
   только отдельным согласованным запуском; он расходует ресурсы backend.

Audit использует стандартный logger `app.agent`, без отдельной БД. Текущий
`logging_setup.py` пишет его в `logs/app.log` и stderr, с суточной ротацией
и хранением 30 файлов. На VPS сохраните защищённые права и этот каталог логов;
при необходимости собирайте stderr в journal.
Записываются capability/scope, числовые ID/даты/pagination, success/failure,
duration и correlation id. Поисковые строки, Bearer, файлы, raw leads и тексты
исключений не записываются. Run id доступен в ответе, не в параметрах audit.

## Локальная проверка

```powershell
venv/Scripts/python.exe -m pytest tests/test_agent_api.py tests/test_agent_service.py tests/test_agent_analytics.py tests/test_agent_cli.py tests/lead_analytics tests/test_lead_analytics_auth.py tests/test_lead_analytics_parity.py -q -p no:cacheprovider --basetemp .tmp-agent-tests
```

Тесты используют искусственные данные и временные БД, без import/startup
`main.py`, production, Prostats, Telegram и Google.

## Настроенный VPS (проверено 4 октября 2026)

На `root@82.147.71.51` checkout расположен в `/opt/MB_LK_leads`, backend —
`lk-backend.service`, один Uvicorn-процесс на `127.0.0.1:8000`.
Из любой директории SSH-сессии доступны:

```bash
lkctl capabilities
lkctl overview
lkctl clients find "Рио-Люкс"
lkctl-compute capabilities
lkctl-compute analytics run --group 8 --run RUN_ID
```

`/usr/local/bin/lkctl` автоматически загружает read-токен, если
`LK_AGENT_TOKEN` ещё не задан. `/usr/local/bin/lkctl-compute` явно выбирает
compute-токен. Значения не нужно копировать в чат или командную строку.
Эти wrappers предназначены для SSH-пользователя root; обычный доступ других
Unix-пользователей к файлам токенов не предоставляется.

Для отдельного ключа Рика используется forced command
`scripts/leadrecord_agent_ssh.py`: доступны только перечисленные в нём
read/compute-команды. Скачивание через этот ключ выполняется командой
`fetch GROUP_ID EXPORT_ID`: stdout содержит байты XLSX, временный файл на VPS
удаляется. `lkctl-compute analytics download --output ...` предназначена для
обычной SSH-сессии оператора; ключ Рика не разрешает запись в произвольный путь.
Bridge сохраняет полученные байты в `runtime/media/owner_generated` и отправляет
документ через существующую очередь доставки владельцу.

Защищённые файлы вне checkout:

- `/etc/leadrecord/agent-api.env` — оба backend-токена;
- `/etc/leadrecord/agent-read.env` — environment read-оператора;
- `/etc/leadrecord/agent-compute.env` — environment compute-оператора.

Каталог имеет права `700`, файлы — `600`, владелец root. Systemd подключает
backend environment через `/etc/systemd/system/lk-backend.service.d/agent-api.conf`.
Основная `.env` не изменялась. Эти файлы входят в защищённый backup конфигурации;
их содержимое не должно попадать в Git, логи или диагностический вывод.

В `/etc/nginx/sites-enabled/leadrecordwh.ru` закрыты `/agent/v1` и
`/api/agent/v1`, включая подпути: существующий proxy убирает префикс `/api/`.
Оба варианта через HTTPS возвращают 404; localhost требует Bearer token.
Резервная копия прежнего Nginx-конфига находится в
`/root/leadrecord-agent-backup-20261004T055632Z/nginx-leadrecordwh.ru`.

После restart проверены read-capabilities, реальная история и результат
существующего отчёта, отказ без токена/с неверным токеном, отказ compute для
read, распознавание compute-токена и запрет mutation payloads. Локальный и
публичный health возвращают 200; audit пишется в существующий `logs/app.log`,
вхождений новых Agent tokens в нём не обнаружено. Реальный анализ клиентских
данных для smoke-проверки не запускался.
