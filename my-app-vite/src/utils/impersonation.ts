// Слушатель получения токена имперсонации из админского окна.
// Ожидаем postMessage { type: 'impersonation-token', access_token: '...' } от origin клиентского портала.

import { isJwtValid } from './jwt';

const TOKEN_KEY = 'access_token';

export function initImpersonationListener() {
  if (typeof window === 'undefined') return;

  const allowedOrigin = window.location.origin; // тот же домен, что и у админа/клиента

  function handleMessage(event: MessageEvent) {
    if (event.origin !== allowedOrigin) return;
    if (!event.data || event.data.type !== 'impersonation-token') return;
    const token = String(event.data.access_token || '');
    if (!token || !isJwtValid(token)) return;
    try {
      sessionStorage.setItem(TOKEN_KEY, token);
      localStorage.setItem(TOKEN_KEY, token);
      // Перезагружаем страницу, чтобы приложение стартовало уже с клиентским токеном.
      window.location.reload();
    } catch {
      /* ignore */
    }
  }

  window.addEventListener('message', handleMessage);
}

