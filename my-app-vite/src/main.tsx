// Файл: src/main.tsx
// Назначение: точка входа фронтенда; подключает обработчики ошибок и рендерит App.
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import App from './App';
import { initClientErrorReporting } from './logger';
import { initImpersonationListener } from './utils/impersonation';
initClientErrorReporting();
initImpersonationListener();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
