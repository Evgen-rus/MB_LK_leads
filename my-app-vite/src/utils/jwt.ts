// Небольшая утилита для разбора JWT и проверки срока годности (exp)
// Без зависимостей: base64url -> JSON

export type JwtPayload = {
  exp?: number; // seconds since epoch
  [key: string]: unknown;
};

function base64UrlToJson<T = unknown>(b64url: string): T | null {
  try {
    const b64 = b64url.replace(/-/g, "+").replace(/_/g, "/");
    const padLen = (4 - (b64.length % 4)) % 4;
    const padded = b64 + "=".repeat(padLen);
    const json = atob(padded);
    return JSON.parse(json) as T;
  } catch {
    return null;
  }
}

export function decodeJwtPayload(token: string): JwtPayload | null {
  const parts = (token || "").split(".");
  if (parts.length < 2) return null;
  return base64UrlToJson<JwtPayload>(parts[1]);
}

export function isJwtValid(token: string): boolean {
  const payload = decodeJwtPayload(token);
  if (!payload || typeof payload.exp !== "number") return false;
  const nowSec = Math.floor(Date.now() / 1000);
  return payload.exp > nowSec;
}

export function getValidTokenFromStorage(): string | null {
  try {
    const token = localStorage.getItem("access_token");
    if (!token) return null;
    return isJwtValid(token) ? token : null;
  } catch {
    return null;
  }
}
