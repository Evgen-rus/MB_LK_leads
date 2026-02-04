# PostgreSQL — установка и настройка (Windows 11 локально + Ubuntu сервер)

Инструкция покрывает: установку PostgreSQL, создание пользователя/БД, фиксацию параметров доступа, проверку подключения, и минимальные настройки проекта (`.env`, `requirements.txt`) для FastAPI + SQLAlchemy + psycopg v3.

---

## 1) Windows 11 (локально)

### 1.1. Скачать PostgreSQL

Скачивать отсюда: [https://www.postgresql.org/download/windows/](https://www.postgresql.org/download/windows/)
На странице выбрать **“Download the installer”** (EDB installer).

---

### 1.2. Установить PostgreSQL

1. Запустить скачанный `.exe` **от имени администратора**.
2. В мастере установки:

   * Компоненты: **PostgreSQL Server**, **Command Line Tools**, **pgAdmin 4** (рекомендуется).
   * **Password** для пользователя `postgres` — задать и **сохранить**.
   * **Port** оставить `5432`.
3. Завершить установку.

---

### 1.3. Проверить, что сервер запущен

#### Вариант A: через службы Windows

1. `Win + R` → `services.msc`
2. Найти службу вида `postgresql-x64-<version>`
   Статус должен быть **Running** (если нет — запустить).

#### Вариант B: через SQL Shell (psql)

1. Пуск → **SQL Shell (psql)**
2. На вопросах Server/Database/Port/Username — нажимать **Enter** (по умолчанию: `localhost`, `postgres`, `5432`, `postgres`)
3. Ввести пароль пользователя `postgres`
4. Должно появиться приглашение: `postgres=#`

---

### 1.4. Создать пользователя и базу данных (dev)

Внутри `psql` (где `postgres=#`) выполнить:

```sql
CREATE USER mb_lk_leads_app WITH PASSWORD 'YOUR_PASSWORD';
CREATE DATABASE mb_lk_leads_dev OWNER mb_lk_leads_app;
GRANT ALL PRIVILEGES ON DATABASE mb_lk_leads_dev TO mb_lk_leads_app;
```

Проверка:

```sql
\du
\l
```

---

### 1.5. Зафиксировать параметры доступа (локально)

* **host:** `127.0.0.1` (или `localhost`)
* **port:** `5432`
* **dbname:** `mb_lk_leads_dev`
* **user:** `mb_lk_leads_app`
* **password:** `YOUR_PASSWORD` (реальный пароль, который вы задали)

---

### 1.6. Проверить вход под пользователем приложения

Выйти из `psql`:

```sql
\q
```

Снова открыть **SQL Shell (psql)** и ввести:

* Server: `localhost`
* Database: `mb_lk_leads_dev`
* Port: `5432`
* Username: `mb_lk_leads_app`
* Password: `YOUR_PASSWORD`

Если видите `mb_lk_leads_dev=>` — готово.

---

### 1.7. pgAdmin: где смотреть БД и таблицы

1. Открыть **pgAdmin 4**
2. В дереве слева:

   * **Servers**

     * (ваш сервер)

       * **Databases**

         * **mb_lk_leads_dev**

           * **Schemas**

             * **public**

               * **Tables**

Данные таблицы: ПКМ по таблице → **View/Edit Data** → **All Rows**.

---

## 2) Ubuntu (облачный сервер)

### 2.1. Установить PostgreSQL

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable postgresql
sudo systemctl start postgresql
sudo systemctl status postgresql --no-pager
```

Проверка:

```bash
pg_isready
```

---

### 2.2. Создать пользователя и базу данных (prod)

Зайти в `psql` под системным пользователем `postgres`:

```bash
sudo -u postgres psql
```

Выполнить:

```sql
CREATE USER mb_lk_leads_app WITH PASSWORD 'YOUR_PASSWORD';
CREATE DATABASE mb_lk_leads_prod OWNER mb_lk_leads_app;
GRANT ALL PRIVILEGES ON DATABASE mb_lk_leads_prod TO mb_lk_leads_app;

\du
\l
```

Выйти:

```sql
\q
```

---

### 2.3. Зафиксировать параметры доступа (сервер)

Если FastAPI и PostgreSQL **на одном сервере** (рекомендуется):

* **host:** `127.0.0.1`
* **port:** `5432`
* **dbname:** `mb_lk_leads_prod`
* **user:** `mb_lk_leads_app`
* **password:** `YOUR_PASSWORD`

---

### 2.4. Проверить подключение на сервере

```bash
psql -h 127.0.0.1 -p 5432 -U mb_lk_leads_app -d mb_lk_leads_prod
```

---

## 3) Опционально: открыть доступ к PostgreSQL извне (если БД на отдельном сервере)

> Рекомендуется **не** открывать порт 5432 “в интернет”. Лучше: ограничить IP, использовать VPN или SSH-туннель.

### 3.1. Разрешить слушать внешний интерфейс

```bash
sudo nano /etc/postgresql/*/main/postgresql.conf
```

Найти/установить:

```conf
listen_addresses = '*'
```

---

### 3.2. Разрешить подключение по IP в `pg_hba.conf`

```bash
sudo nano /etc/postgresql/*/main/pg_hba.conf
```

Добавить (пример: разрешить только IP вашего приложения):

```conf
host    mb_lk_leads_prod   mb_lk_leads_app   YOUR_APP_PUBLIC_IP/32   scram-sha-256
```

---

### 3.3. Перезапуск PostgreSQL

```bash
sudo systemctl restart postgresql
```

---

### 3.4. Firewall (ufw), если включен

```bash
sudo ufw allow 5432/tcp
sudo ufw status
```

---

## 4) Настройки проекта (FastAPI + SQLAlchemy + psycopg v3)

### 4.1. `.env`

Локально (dev):

```env
DATABASE_URL=postgresql+psycopg://mb_lk_leads_app:PASSWORD@127.0.0.1:5432/mb_lk_leads_dev
```

Сервер (prod):

```env
DATABASE_URL=postgresql+psycopg://mb_lk_leads_app:PASSWORD@127.0.0.1:5432/mb_lk_leads_prod
```

> Вместо `PASSWORD` подставьте реальный пароль.

---

### 4.2. `requirements.txt`

```txt
psycopg[binary]==3.3.2
```

---

## 5) Быстрый чек-лист “готово”

1. PostgreSQL установлен и сервис запущен (Windows: службы / Ubuntu: systemctl).
2. Созданы пользователь и база (`\du`, `\l`).
3. Проверен логин под пользователем приложения в нужную БД.
4. Зафиксированы параметры доступа (host/port/db/user/password).
5. В `.env` прописан `DATABASE_URL` вида `postgresql+psycopg://...`.
6. В `requirements.txt` есть `psycopg[binary]==3.3.2`, зависимости установлены (`pip install -r requirements.txt`).
