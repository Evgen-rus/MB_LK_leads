[4 tools called]

Отлично! У вас есть полноценное веб-приложение с React фронтендом и FastAPI бэкендом. Чтобы ваш сайт открывался на купленном домене, нужно выполнить несколько шагов. Вот полная инструкция:

## 1. **Выберите хостинг/сервер**

Вам понадобится VPS (виртуальный сервер) или хостинг, где можно запустить:
- **Frontend**: статические файлы (HTML/CSS/JS)
- **Backend**: Python приложение на FastAPI

**Рекомендации:**
- **DigitalOcean Droplet** ($6/месяц)
- **Vultr** ($6/месяц) 
- **Hetzner** (€3.5/месяц)
- **Railway** или **Render** (для быстрого старта)

## 2. **Настройте DNS**

После покупки домена у регистратора (GoDaddy, Namecheap и т.д.):

**В панели регистратора добавьте A-запись:**
```
Тип: A
Имя: @
Значение: IP-адрес вашего сервера
TTL: 3600 (или авто)
```

**Пример:**
```
yourdomain.com A 123.456.789.0
```

## 3. **Разверните приложение на сервере**

### **Вариант 1: Простой (с Nginx + Gunicorn)**

1. **Установите зависимости:**
```bash
sudo apt update
sudo apt install python3 python3-pip nginx git
```

2. **Клонируйте проект:**
```bash
git clone ваш-репозиторий
cd ваш-проект
```

3. **Настройте бэкенд:**
```bash
pip install -r requirements.txt
# Настройте .env файл с вашими данными
```

4. **Соберите фронтенд:**
```bash
cd my-app-vite
npm install
npm run build
```

5. **Настройте Nginx (файл `/etc/nginx/sites-available/yourdomain.com`):**
```nginx
server {
    listen 80;
    server_name yourdomain.com;

    # Frontend
    location / {
        root /path/to/your/project/my-app-vite/dist;
        try_files $uri $uri/ /index.html;
    }

    # Backend API
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

6. **Запустите сервисы:**
```bash
# Бэкенд через Gunicorn
gunicorn backend.app.main:app -w 4 -k uvicorn.workers.UvicornWorker -b 127.0.0.1:8000

# Nginx
sudo systemctl enable nginx
sudo systemctl start nginx
```

### **Вариант 2: Современный (Docker)**

Создайте `docker-compose.yml`:
```yaml
version: '3.8'
services:
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=sqlite:///./app.db
      - CORS_ORIGINS=https://yourdomain.com
    volumes:
      - ./app.db:/app/app.db

  frontend:
    build: ./my-app-vite
    ports:
      - "3000:80"
```

## 4. **Настройте HTTPS (SSL)**

**Бесплатно через Let's Encrypt:**
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com
```

## 5. **Обновите конфигурацию**

В вашем `.env` файле измените:
```
CORS_ORIGINS=https://yourdomain.com
COOKIE_SECURE=1
```

## **Быстрые альтернативы:**

### **Railway (самый простой):**
1. Загрузите код на GitHub
2. Подключите Railway к репозиторию
3. Оно автоматически развернёт и даст домен
4. Потом подключите свой купленный домен

### **Vercel + Railway:**
- **Vercel** для фронтенда (бесплатно)
- **Railway** для бэкенда ($5/месяц)