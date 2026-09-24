# MB_LK_leads

Личный кабинет для работы с проектами и лидами: FastAPI backend и React/Vite frontend с интеграциями Prostats, Telegram и Google Sheets.

## Запуск локально

Настройте локальный `.env` с необходимыми секретами: `WEBHOOK_SECRET` обязателен для запуска backend. Не добавляйте `.env` в Git.

```bash
python -m pip install -r requirements.txt
python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

В отдельном терминале:

```bash
cd my-app-vite
npm ci
npm run dev
npm run dev -- --host 127.0.0.1 --port 5174
```

Frontend доступен по `http://localhost:5173`, API — по `http://localhost:8000`, health-check — `http://localhost:8000/health`.

Правила работы с репозиторием: [AGENTS.md](AGENTS.md). Архитектурный контекст: [ARCHITECTURE.md](ARCHITECTURE.md).
