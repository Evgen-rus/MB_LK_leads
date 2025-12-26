// Файл: src/logger.ts
// Назначение: сбор JS-ошибок на фронте.
// Отправка: всегда в production; в dev — если VITE_REPORT_ERRORS=true в .env.local
import { sendClientError } from './api';

const env = import.meta.env as Record<string, unknown>;
const isProd =
  env.PROD === true ||
  env.MODE === 'production' ||
  env.MODE === 'prod';
const reportFlag = String(env.VITE_REPORT_ERRORS ?? '').toLowerCase();
const reportEnabled = isProd || reportFlag === '1' || reportFlag === 'true';

function nowIso(): string {
  return new Date().toISOString();
}

export function initClientErrorReporting() {
  // В проде всегда отправляем; в dev — если включён VITE_REPORT_ERRORS
  if (!reportEnabled) return;

  window.addEventListener('error', (event) => {
    try {
      const msg = event?.error?.message || event?.message || 'Unhandled error';
      const stack = event?.error?.stack || undefined;
      sendClientError({
        message: String(msg),
        stack,
        url: window.location.href,
        userAgent: navigator.userAgent,
        level: 'error',
        time: nowIso(),
      }).catch((err) => {
        if (!isProd) console.warn('Не удалось отправить ошибку клиента', err);
      });
    } catch (err) {
      if (!isProd) console.error('Ошибка обработки window.error', err);
    }
  });

  window.addEventListener('unhandledrejection', (event: PromiseRejectionEvent) => {
    try {
      const reason: unknown = event.reason;
      const msg =
        typeof reason === 'string'
          ? reason
          : (typeof reason === 'object' && reason && 'message' in reason
              ? String((reason as { message?: unknown }).message)
              : 'Unhandled rejection');
      const stack =
        typeof reason === 'object' && reason && 'stack' in reason
          ? String((reason as { stack?: unknown }).stack ?? '')
          : undefined;
      sendClientError({
        message: String(msg),
        stack,
        url: window.location.href,
        userAgent: navigator.userAgent,
        level: 'error',
        time: nowIso(),
      }).catch((err) => {
        if (!isProd) console.warn('Не удалось отправить unhandled rejection', err);
      });
    } catch (err) {
      if (!isProd) console.error('Ошибка обработки unhandledrejection', err);
    }
  });
}

// Можно экспортировать простой логгер при желании
export const logger = {
  info: (...args: unknown[]) => { if (!isProd) console.info('[app]', ...args); },
  warn: (...args: unknown[]) => { if (!isProd) console.warn('[app]', ...args); },
  error: (...args: unknown[]) => { if (!isProd) console.error('[app]', ...args); },
};


