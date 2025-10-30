import { sendClientError } from './api';

const isProd = (import.meta as any).env?.PROD === true || (import.meta as any).env?.MODE === 'production';
const reportFlag = String((import.meta as any).env?.VITE_REPORT_ERRORS || '').toLowerCase();
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
      }).catch(() => {});
    } catch {}
  });

  window.addEventListener('unhandledrejection', (event: PromiseRejectionEvent) => {
    try {
      const reason: any = event.reason;
      const msg = typeof reason === 'string' ? reason : (reason?.message || 'Unhandled rejection');
      const stack = typeof reason === 'object' ? (reason?.stack || undefined) : undefined;
      sendClientError({
        message: String(msg),
        stack,
        url: window.location.href,
        userAgent: navigator.userAgent,
        level: 'error',
        time: nowIso(),
      }).catch(() => {});
    } catch {}
  });
}

// Можно экспортировать простой логгер при желании
export const logger = {
  info: (...args: unknown[]) => { if (!isProd) console.info('[app]', ...args); },
  warn: (...args: unknown[]) => { if (!isProd) console.warn('[app]', ...args); },
  error: (...args: unknown[]) => { if (!isProd) console.error('[app]', ...args); },
};


