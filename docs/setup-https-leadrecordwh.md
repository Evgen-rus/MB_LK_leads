# 🚀 Инструкция: как включить HTTPS (Let’s Encrypt + Certbot)

### для домена: **leadrecordwh.ru**

### на сервере с **nginx + FastAPI**

---

## 📌 0. Предварительные условия

У тебя должен быть:

* домен, указывающий на IP сервера
* сервер с Ubuntu
* запущенный nginx на порту 80
* бекенд (uvicorn) работает за nginx, но это необязательно

Проверка:

```
ping leadrecordwh.ru
```

---

## 🧰 1. Обновить список пакетов

```
sudo apt update
```

Зачем: обновляет индексы пакетов, чтобы система видела актуальные версии.

---

## 🔧 2. Установить Certbot и плагин nginx

```
sudo apt install certbot python3-certbot-nginx -y
```

Зачем:

* certbot — утилита для получения сертификатов
* плагин — связывает Certbot с nginx

---

## 🔐 3. Выпустить SSL сертификат для домена

```
sudo certbot --nginx -d leadrecordwh.ru -d www.leadrecordwh.ru
```

Certbot спросит:

### Ввести email

Пиши свою рабочую почту

### Согласие с условиями

```
Y
```

### Подписаться на новости?

```
N
```

### Включить редирект HTTP→HTTPS?

Выбирай:

```
2
```

(или Yes → Redirect)

После этого Certbot:

* проверит домен
* выпустит сертификат
* запишет пути в nginx
* перезапустит nginx

---

## 🎉 4. Проверить, что HTTPS работает

### Проверка в браузере

Открыть:

```
https://leadrecordwh.ru
```

Должен быть замочек, без предупреждений.

### Проверка в терминале

Слушает ли nginx порт 443:

```
sudo lsof -i :443
```

Проверить синтаксис nginx:

```
sudo nginx -t
```

---

## 🔁 5. Что сделал Certbot автоматически

* создал сертификаты в `/etc/letsencrypt/live/leadrecordwh.ru`
* прописал SSL в `/etc/nginx/sites-enabled/leadrecordwh.ru`
* создал systemd-таймер автопродления
* включил редирект с http на https

---

## 🔁 6. Проверка автопродления

Проверить таймер:

```
systemctl list-timers | grep certbot
```

Тестовое продление:

```
sudo certbot renew --dry-run
```

---

## 📍 7. Где лежат важные файлы

| Что               | Где                                                   |
| ----------------- | ----------------------------------------------------- |
| Конфиг nginx      | `/etc/nginx/sites-enabled/leadrecordwh.ru`            |
| Полный сертификат | `/etc/letsencrypt/live/leadrecordwh.ru/fullchain.pem` |
| Приватный ключ    | `/etc/letsencrypt/live/leadrecordwh.ru/privkey.pem`   |
| Лог certbot       | `/var/log/letsencrypt/letsencrypt.log`                |

---

## ☑ 8. Когда всё готово

Теперь можно использовать:

```
https://leadrecordwh.ru/webhooks/something
```

для:

* вебхуков
* API
* интеграций
* Telegram-ботов
* Bitrix24
* Make/Zapier

---

## 🧠 9. Повторение процесса для любого другого сайта

Просто меняешь домены:

```
sudo certbot --nginx -d example.com -d www.example.com
```

---

## 💡 10. Полный список команд для копирования

(минимальный набор)

```bash
sudo apt update
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d leadrecordwh.ru -d www.leadrecordwh.ru
sudo nginx -t
sudo systemctl reload nginx
sudo certbot renew --dry-run
```

---

# 🎯 Финальный вывод

Ты:

✔ получил бесплатный SSL
✔ перевёл сайт на HTTPS
✔ включил автопродление
✔ готов принимать вебхуки и API запросы

И главное — без поломок, без лишней настройки.