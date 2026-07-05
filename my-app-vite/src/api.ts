// Файл: src/api.ts
// Назначение: HTTP-клиент фронтенда для работы с бэкендом (projects, client-errors).
import type { Project, ProjectMutableStatus, ProjectStatus } from './types/project';

// Базовый URL для API:
// - в проде берётся из Vite-переменной окружения VITE_API_BASE (например, "/api")
// - дополнительно, если страница открыта на leadrecordwh.ru — форсируем "/api"
// - локально по умолчанию используется http://localhost:8000
// Важно использовать именно import.meta.env, чтобы Vite смог подставить значение на этапе сборки.
const RUNTIME_HOST =
  typeof window !== 'undefined' ? window.location.hostname : undefined;

type HttpError = Error & {
  status?: number;
  isNetworkError?: boolean;
  errorDetail?: string | null;
  errorCode?: string | null;
};

const env = import.meta.env as Record<string, unknown>;
const API_BASE =
  // 1) Явное значение из .env/.env.production
  (typeof env.VITE_API_BASE === 'string' ? env.VITE_API_BASE : undefined) ||
  // 2) Если мы на прод-домене — всегда ходим через /api (через Nginx)
  (RUNTIME_HOST === 'leadrecordwh.ru' ? '/api' : undefined) ||
  // 3) Фолбэк для локальной разработки
  'http://localhost:8000';

const PROJECT_PROVIDER_UNAVAILABLE_MESSAGE =
  'Сервис поставщика временно недоступен. Создание и редактирование проектов временно не работает. Попробуйте повторить через 15 минут.';

const GENERIC_SERVICE_UNAVAILABLE_MESSAGE =
  'Сервис временно недоступен. Попробуйте повторить операцию через 15 минут.';

function isGatewayUnavailableStatus(status: number): boolean {
  return status === 502 || status === 503 || status === 504;
}

function looksLikeHtmlError(text: string): boolean {
  const trimmed = text.trim().toLowerCase();
  return (
    trimmed.startsWith('<!doctype html') ||
    trimmed.startsWith('<html') ||
    trimmed.includes('<title>504 gateway time-out</title>') ||
    trimmed.includes('<h1>504 gateway time-out</h1>') ||
    trimmed.includes('nginx')
  );
}

function isProjectMutationRequest(path: string, init?: RequestInit): boolean {
  const method = (init?.method || 'GET').toUpperCase();
  if (method === 'POST' && path === '/projects') return true;
  if (method === 'PATCH' && /^\/projects\/\d+(?:$|[?#])/.test(path)) return true;
  if (method === 'PATCH' && /^\/admin\/projects\/\d+(?:$|[?#])/.test(path)) return true;
  return false;
}

function unavailableMessageFor(path: string, init: RequestInit | undefined): string {
  return isProjectMutationRequest(path, init)
    ? PROJECT_PROVIDER_UNAVAILABLE_MESSAGE
    : GENERIC_SERVICE_UNAVAILABLE_MESSAGE;
}

function buildAccessTokenCookie(token: string, opts?: { expires?: Date }): string {
  // Cookie используется только для скачивания файлов через window.open,
  // потому что в таком запросе нельзя передать Authorization-заголовок.
  //
  // Secure-cookie НЕ отправляется браузером по http://, поэтому добавляем Secure
  // только если сайт открыт по https://.
  const parts: string[] = [`access_token=${encodeURIComponent(token)}`, 'path=/'];

  if (opts?.expires) {
    parts.push(`expires=${opts.expires.toUTCString()}`);
  }

  // Для скачивания достаточно Lax: cookie отправится при переходе/открытии вкладки на наш домен.
  // Strict иногда ломает сценарии (например, переход из внешнего домена).
  parts.push('samesite=lax');

  if (typeof window !== 'undefined' && window.location.protocol === 'https:') {
    parts.push('secure');
  }

  return parts.join('; ');
}

function setAccessTokenCookie(token: string): void {
  document.cookie = buildAccessTokenCookie(token);
}

function clearAccessTokenCookie(): void {
  const expired = new Date(0);
  // Стираем и "secure", и "не secure" варианты (secure зависит от протокола страницы).
  document.cookie = buildAccessTokenCookie('', { expires: expired });
  // На всякий случай пробуем ещё и самым простым способом.
  document.cookie = `access_token=; path=/; expires=${expired.toUTCString()}; samesite=lax`;
}

export type Day = 'Пн'|'Вт'|'Ср'|'Чт'|'Пт'|'Сб'|'Вс';
export type CollectionSource = 'Сайты'|'Звонки'|'СМС'|'Ретросайты'|'Ретрозвонки'|'Пересечение'|'Пиксель';

export type CreateProjectItem = {
  name: string;
  tag: string;
  collectionSource: CollectionSource;
  dataSourceCode: 'B1'|'B2'|'B3'|'B4'|'UNMAPPED';
  dataLimit: number;
  status: ProjectMutableStatus;
  regionMode: 'include'|'exclude';
  regions: string[];
  sites?: string[];
  phones?: string[];
  smsSenderName?: string;
  days: Day[];
};

export type ProjectUpdatePayload = {
  name: string;
  tag: string;
  status: ProjectMutableStatus;
  dataLimit: number;
  regionMode: 'include'|'exclude';
  regions: string[];
  sites?: string[];
  phones?: string[];
  smsSenderName?: string;
  days: Day[];
};

function inferFilenameFromContentDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback;
  // Пробуем вытащить filename=... (простые случаи).
  const m = header.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i);
  if (!m) return fallback;
  try {
    return decodeURIComponent(m[1].trim().replace(/"$/g, '')) || fallback;
  } catch {
    return m[1].trim().replace(/"$/g, '') || fallback;
  }
}

async function downloadByUrl(url: string, filenameFallback: string): Promise<void> {
  const token = localStorage.getItem('access_token');
  const headers: Record<string, string> = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(url, { headers });
  if (!res.ok) {
    // Пытаемся вытащить detail из JSON (как делает http()).
    const text = await res.text().catch(() => '');
    let msg = res.statusText || 'Ошибка скачивания';
    try {
      const j = text ? (JSON.parse(text) as { detail?: string; message?: string }) : null;
      msg = j?.detail || j?.message || text || msg;
    } catch {
      msg = text || msg;
    }
    const err: HttpError = Object.assign(new Error(msg), { status: res.status, errorDetail: msg });
    throw err;
  }

  const blob = await res.blob();
  const filename = inferFilenameFromContentDisposition(res.headers.get('content-disposition'), filenameFallback);

  const href = URL.createObjectURL(blob);
  try {
    const a = document.createElement('a');
    a.href = href;
    a.download = filename;
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    // Освобождаем объектный URL чуть позже, чтобы браузер успел начать скачивание.
    window.setTimeout(() => URL.revokeObjectURL(href), 5_000);
  }
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    const token = localStorage.getItem('access_token');
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    res = await fetch(`${API_BASE}${path}`, {
      headers,
      ...init,
    });
  } catch {
    // Сетевая ошибка (нет интернета, сервер недоступен)
    const err: HttpError = Object.assign(
      new Error('Нет соединения с сервером. Проверьте подключение к интернету.'),
      { status: 0, isNetworkError: true },
    );
    throw err;
  }

  if (!res.ok) {
    const errorMessage = res.statusText;
    let errorDetail: string | null = null;
    let errorCode: string | null = null;

    function buildDuplicateMessage(
      message: string,
      duplicates: Record<string, string[]>,
      pathValue: string,
      methodValue: string,
    ): string {
      const lower = message.toLowerCase();
      const label = lower.includes('домены')
        ? 'домены'
        : lower.includes('номера')
          ? 'номера'
          : 'значения';
      let actionPrefix = 'Операция не выполнена';
      if (methodValue === 'POST' && pathValue.startsWith('/projects')) {
        actionPrefix = 'Проект не создан';
      } else if (methodValue === 'PATCH' && (pathValue.startsWith('/projects') || pathValue.startsWith('/admin/projects'))) {
        actionPrefix = 'Проект не обновлён';
      }
      const lines = Object.entries(duplicates).map(
        ([item, projects]) => `${item} -> ${projects.join(', ')}`,
      );
      return `${actionPrefix}. Эти ${label} уже используются:\n${lines.join('\n')}\nУдалите их из других проектов или укажите другие.`;
    }

    function parseErrorDetail(text: string): string {
      try {
        const json = JSON.parse(text) as {
          detail?: unknown;
          message?: unknown;
        };

        if (json.detail && typeof json.detail === 'object') {
          const detail = json.detail as {
            message?: string;
            code?: string;
            duplicates?: Record<string, string[]>;
          };
          errorCode = typeof detail.code === 'string' ? detail.code : null;
          if (detail.duplicates && Object.keys(detail.duplicates).length > 0) {
            const methodValue = (init?.method || 'GET').toUpperCase();
            return buildDuplicateMessage(detail.message || 'Ошибка', detail.duplicates, path, methodValue);
          }
          return detail.message || JSON.stringify(json.detail);
        }

        if (typeof json.detail === 'string') return json.detail;
        if (typeof json.message === 'string') return json.message;
        return text;
      } catch {
        return text;
      }
    }

    try {
      const text = await res.text();
      if (text) {
        errorDetail = parseErrorDetail(text);
        if (looksLikeHtmlError(text) && isGatewayUnavailableStatus(res.status)) {
          errorDetail = unavailableMessageFor(path, init);
        }
      }
    } catch {
      errorDetail = null;
    }

    if (isGatewayUnavailableStatus(res.status) && isProjectMutationRequest(path, init)) {
      errorDetail = unavailableMessageFor(path, init);
    }

    if (res.status === 401) {
      // токен недействителен — очищаем и кидаем 401
      try {
        localStorage.removeItem('access_token');
        clearAccessTokenCookie();
      } catch (err) {
        console.warn('Не удалось очистить токен', err);
      }
    }
    const err: HttpError = Object.assign(new Error(errorDetail || errorMessage), {
      status: res.status,
      errorDetail,
      errorCode,
    });
    throw err;
  }
  return res.json();
}

async function httpForm<T>(path: string, formData: FormData, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    const headers: Record<string, string> = {};
    const token = localStorage.getItem('access_token');
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    res = await fetch(`${API_BASE}${path}`, {
      method: init?.method || 'POST',
      headers,
      body: formData,
      ...init,
    });
  } catch {
    const err: HttpError = Object.assign(
      new Error('Нет соединения с сервером. Проверьте подключение к интернету.'),
      { status: 0, isNetworkError: true },
    );
    throw err;
  }

  if (!res.ok) {
    let errorDetail: string | null = null;
    try {
      const text = await res.text();
      if (text) {
        try {
          const json = JSON.parse(text) as { detail?: unknown; message?: unknown };
          if (typeof json.detail === 'string') errorDetail = json.detail;
          else if (typeof json.message === 'string') errorDetail = json.message;
          else errorDetail = text;
        } catch {
          errorDetail = text;
        }
      }
    } catch {
      errorDetail = null;
    }

    if (res.status === 401) {
      try {
        localStorage.removeItem('access_token');
        clearAccessTokenCookie();
      } catch (err) {
        console.warn('Не удалось очистить токен', err);
      }
    }

    const err: HttpError = Object.assign(new Error(errorDetail || res.statusText), {
      status: res.status,
      errorDetail,
    });
    throw err;
  }

  return res.json();
}

export type ProjectListResp = { items: Project[]; total: number };
export type CreateProjectsResp = { items: Project[]; warning?: string | null };
export type UpdateProjectResp = { project: Project; warning?: string | null };
export type ProjectSortBy =
  | 'id'
  | 'name'
  | 'dataSourceCode'
  | 'status'
  | 'dataLimit'
  | 'collectionSource'
  | 'sourcesCount'
  | 'createdAt'
  | 'numbersPeriod'
  | 'numbersTotal';
export type SortDir = 'asc' | 'desc';

export async function fetchProjects(params?: {
  offset?: number;
  limit?: number;
  q?: string;
  sources?: string[];
  collectionSources?: string[];
  fromDate?: string;
  toDate?: string;
  includeDeleted?: boolean;
  projectStatus?: ProjectStatus;
  dailyLimitReached?: boolean;
  isTop?: boolean;
  sortBy?: ProjectSortBy;
  sortDir?: SortDir;
}): Promise<ProjectListResp> {
  const q = new URLSearchParams();
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  if (params?.q) q.set('q', params.q);
  if (params?.sources && params.sources.length > 0) q.set('sources', params.sources.join(','));
  if (params?.collectionSources && params.collectionSources.length > 0) q.set('collectionSources', params.collectionSources.join(','));
  if (params?.fromDate) q.set('fromDate', params.fromDate);
  if (params?.toDate) q.set('toDate', params.toDate);
  if (params?.includeDeleted) q.set('includeDeleted', 'true');
  if (params?.projectStatus) q.set('projectStatus', params.projectStatus);
  if (params?.dailyLimitReached) q.set('dailyLimitReached', 'true');
  if (params?.isTop) q.set('isTop', 'true');
  if (params?.sortBy) q.set('sortBy', params.sortBy);
  if (params?.sortDir) q.set('sortDir', params.sortDir);
  const qs = q.toString();
  return http<ProjectListResp>(`/projects${qs ? `?${qs}` : ''}`);
}

export async function createProjects(items: CreateProjectItem[]): Promise<CreateProjectsResp> {
  return http<CreateProjectsResp>('/projects', {
    method: 'POST',
    body: JSON.stringify({ items }),
  });
}

export async function updateProject(id: number, payload: ProjectUpdatePayload): Promise<UpdateProjectResp> {
  return http<UpdateProjectResp>(`/projects/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function setProjectTop(id: number, isTop: boolean): Promise<Project> {
  return http<Project>(`/projects/${id}/top`, {
    method: 'PATCH',
    body: JSON.stringify({ isTop }),
  });
}

export async function deleteProject(id: number): Promise<void> {
  await http(`/projects/${id}`, { method: 'DELETE' });
}

export async function sendClientError(payload: {
  message: string;
  stack?: string;
  url?: string;
  userAgent?: string;
  level?: 'error'|'warn'|'info';
  time?: string;
}): Promise<void> {
  await http('/client-errors', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function login(username: string, password: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ login: username, password }),
  });
  if (!resp.ok) {
    const text = await resp.text();
    let msg = resp.statusText;
    try {
      const j = text ? (JSON.parse(text) as { detail?: string }) : null;
      msg = j?.detail || msg;
    } catch (parseErr) {
      console.warn('Не удалось распарсить ответ логина', parseErr);
    }
    const err: HttpError = Object.assign(new Error(msg), { status: resp.status });
    throw err;
  }
  const data = await resp.json() as { access_token: string };
  localStorage.setItem('access_token', data.access_token);
  // Сохраняем токен в cookies для безопасного экспорта файлов
  setAccessTokenCookie(data.access_token);
}

export async function logout(): Promise<void> {
  try {
    localStorage.removeItem('access_token');
    // Очищаем cookie с токеном
    clearAccessTokenCookie();
  } catch (err) {
    console.warn('Не удалось очистить токен при выходе', err);
  }
}

// -------- Профиль текущего пользователя --------
export type MeResponse = {
  id: number;
  login: string;
  name?: string | null;
  role: 'admin' | 'client' | 'agent';
  ownerAgentId?: number | null;
  viaImpersonation: boolean;
  impersonatorUserId?: number | null;
  isDisabled: boolean;
  projectsMutationLocked: boolean;
  projectsMutationLockedAt?: string | null;
  projectsMutationLockedBy?: number | null;
  projectsMutationLockReason?: string | null;
  autoLimitControlEnabled: boolean;
  telegramNotificationsChatId?: string | null;
  telegramAutoPauseEnabled: boolean;
  uniqueProjectNamesEnabled: boolean;
  pixelTableUrl?: string | null;
};

export async function fetchMe(): Promise<MeResponse> {
  return http<MeResponse>('/me');
}

// -------- Имперсонация клиента админом --------
export async function impersonateClient(clientId: number): Promise<{ access_token: string; ttl_minutes: number }> {
  return http<{ access_token: string; ttl_minutes: number }>(`/admin/clients/${clientId}/impersonate`, {
    method: 'POST',
  });
}

export async function impersonateAgent(agentId: number): Promise<{ access_token: string; ttl_minutes: number }> {
  return http<{ access_token: string; ttl_minutes: number }>(`/admin/agents/${agentId}/impersonate`, {
    method: 'POST',
  });
}


// -------- Лиды --------
export type Lead = {
  ext_id: string;
  lk_id: string;
  project_id?: number | null;
  project_name?: string | null;
  created_at: string; // дата в ЛК (момент записи в БД, = imported_at)
  imported_at: string; // время попадания в БД
  phone: string;
  utm_campaign?: string | null;
  source?: string | null;
  lead_source?: 'provider' | 'pixel';
  pixel_url?: string | null;
  collection_source?: string | null;
};

export type LeadsListResp = { items: Lead[]; total: number };

export async function fetchLeads(params: { projectIds?: number[]; sources?: string[]; collectionSources?: string[]; fromDate: string; toDate: string; q?: string; offset?: number; limit?: number; }): Promise<LeadsListResp> {
  const q = new URLSearchParams({
    fromDate: params.fromDate,
    toDate: params.toDate,
  });
  if (params.projectIds && params.projectIds.length > 0) {
    q.set('projectIds', params.projectIds.join(','));
  }
  if (params.sources && params.sources.length > 0) {
    q.set('sources', params.sources.join(','));
  }
  if (params.collectionSources && params.collectionSources.length > 0) {
    q.set('collectionSources', params.collectionSources.join(','));
  }
  if (params.q && params.q.trim()) q.set('q', params.q.trim());
  if (params.offset != null) q.set('offset', String(params.offset));
  if (params.limit != null) q.set('limit', String(params.limit));
  return http<LeadsListResp>(`/leads?${q.toString()}`);
}


// -------- История изменений проектов --------
export type ProjectHistoryItem = {
  id: number;
  eventId: string;
  action: 'create' | 'update' | 'delete';
  createdAt: string;      // 'YYYY-MM-DD HH:MM:SS'
  description: string;    // краткое текстовое описание изменения
  outcome?: 'success' | 'failed';
  errorMessage?: string | null;
  actor?: UserInfo | null;
  actorMode?: 'client' | 'admin' | 'admin_impersonation' | null;
};

export async function fetchProjectHistory(projectId: number, limit: number = 100): Promise<ProjectHistoryItem[]> {
  const q = new URLSearchParams();
  if (limit) q.set('limit', String(limit));
  const qs = q.toString();
  return http<ProjectHistoryItem[]>(`/projects/${projectId}/history${qs ? `?${qs}` : ''}`);
}


// -------- Отчёты (экспорт) --------
export type ReportItem = {
  id: number;
  createdAt: string;
  fromDate: string;
  toDate: string;
  projectIds?: string | null;
  format: string;
};

export type ReportsListResp = {
  items: ReportItem[];
  total: number;
};

export async function fetchReports(params?: {
  offset?: number;
  limit?: number;
  fromDate?: string;
  toDate?: string;
}): Promise<ReportsListResp> {
  const q = new URLSearchParams();
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  if (params?.fromDate) q.set('fromDate', params.fromDate);
  if (params?.toDate) q.set('toDate', params.toDate);
  const qs = q.toString();
  return http<ReportsListResp>(`/reports${qs ? `?${qs}` : ''}`);
}

export async function createReport(payload: { fromDate: string; toDate: string; projectIds?: number[]; format: 'csv' | 'xlsx'; }): Promise<ReportItem> {
  return http<ReportItem>('/reports', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// -------- История активности (клиент) --------
export type ActivityEntity = 'project' | 'blacklist' | 'balance' | 'report';

export type ActivityEvent = {
  eventId: string;         // AE-44 / BO-1 / RE-9
  sourceId: number;
  entity: ActivityEntity;
  action: string;
  createdAt: string;       // YYYY-MM-DD HH:mm:ss
  client?: UserInfo | null; // клиент, к которому относится событие (для manager-зоны)
  actor?: UserInfo | null; // кто выполнил действие
  description: string;     // краткое описание
  outcome?: 'success' | 'failed';
  errorMessage?: string | null;
  status?: 'pending' | 'done';
  projectId?: number | null;
  projectName?: string | null;
  periodFrom?: string | null;
  periodTo?: string | null;
};

export type ActivityEventsListResp = {
  items: ActivityEvent[];
  total: number;
};

export async function fetchClientActivityEvents(params?: {
  offset?: number;
  limit?: number;
  fromDate?: string;
  toDate?: string;
  entities?: ActivityEntity[];
  q?: string;
}): Promise<ActivityEventsListResp> {
  const q = new URLSearchParams();
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  if (params?.fromDate) q.set('fromDate', params.fromDate);
  if (params?.toDate) q.set('toDate', params.toDate);
  if (params?.entities && params.entities.length > 0) q.set('entities', params.entities.join(','));
  if (params?.q && params.q.trim()) q.set('q', params.q.trim());
  const qs = q.toString();
  return http<ActivityEventsListResp>(`/activity/events${qs ? `?${qs}` : ''}`);
}

export async function fetchAdminActivityEvents(params?: {
  offset?: number;
  limit?: number;
  fromDate?: string;
  toDate?: string;
  clientId?: number;
  entities?: ActivityEntity[];
  q?: string;
  status?: 'all' | 'success' | 'failed' | 'pending' | 'done';
}): Promise<ActivityEventsListResp> {
  const q = new URLSearchParams();
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  if (params?.fromDate) q.set('fromDate', params.fromDate);
  if (params?.toDate) q.set('toDate', params.toDate);
  if (params?.clientId != null) q.set('clientId', String(params.clientId));
  if (params?.entities && params.entities.length > 0) q.set('entities', params.entities.join(','));
  if (params?.q && params.q.trim()) q.set('q', params.q.trim());
  if (params?.status && params.status !== 'all') q.set('status', params.status);
  const qs = q.toString();
  return http<ActivityEventsListResp>(`/admin/activity/events${qs ? `?${qs}` : ''}`);
}

export type HistoryEventDetail = ProjectChangeCardData & {
  eventId: string;
  source: 'audit' | 'project_operation';
  sourceId: number;
};

export async function fetchHistoryEventDetail(eventId: string): Promise<HistoryEventDetail> {
  return http<HistoryEventDetail>(`/activity/events/${encodeURIComponent(eventId)}`);
}

export function buildLeadsExportUrl(params: {
  projectIds?: number[];
  sources?: string[];
  collectionSources?: string[];
  fromDate: string;
  toDate: string;
  format: 'csv'|'xlsx';
  source?: 'leads' | 'reports';
  clientId?: number;
}): string {
  const q = new URLSearchParams({ fromDate: params.fromDate, toDate: params.toDate, format: params.format });
  if (params.projectIds && params.projectIds.length > 0) q.set('projectIds', params.projectIds.join(','));
  if (params.sources && params.sources.length > 0) q.set('sources', params.sources.join(','));
  if (params.collectionSources && params.collectionSources.length > 0) q.set('collectionSources', params.collectionSources.join(','));
  if (params.source) q.set('source', params.source);
  if (params.clientId != null) q.set('clientId', String(params.clientId));

  // Токен теперь передается через cookies, а не в URL (для безопасности)
  // Сервер автоматически прочитает токен из cookies при скачивании файла
  return `${API_BASE}/leads/export?${q.toString()}`;
}

export async function downloadLeadsExport(params: {
  projectIds?: number[];
  sources?: string[];
  collectionSources?: string[];
  fromDate: string;
  toDate: string;
  format: 'csv' | 'xlsx';
  source?: 'leads' | 'reports';
  clientId?: number;
}): Promise<void> {
  const url = buildLeadsExportUrl(params);
  const fallbackName = `leads_${params.fromDate}_${params.toDate}.${params.format}`;
  await downloadByUrl(url, fallbackName);
}


// -------- Черный список --------
export type BlacklistPhone = {
  id: number;
  phone: string;
  createdAt: string;
};

export type BlacklistListResp = {
  items: BlacklistPhone[];
  total: number;
};

export async function listBlacklist(params?: { offset?: number; limit?: number; q?: string }): Promise<BlacklistListResp> {
  const q = new URLSearchParams();
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  if (params?.q) q.set('q', params.q);
  const qs = q.toString();
  return http<BlacklistListResp>(`/blacklist${qs ? `?${qs}` : ''}`);
}

export async function addToBlacklist(phones: string[]): Promise<BlacklistPhone[]> {
  return http<BlacklistPhone[]>('/blacklist', {
    method: 'POST',
    body: JSON.stringify({ phones }),
  });
}

export async function deleteFromBlacklist(id: number): Promise<void> {
  await http(`/blacklist/${id}`, { method: 'DELETE' });
}


// -------- Поддержка / чат --------
export async function sendSupportMessage(payload: { phone: string; text: string }): Promise<void> {
  await http('/support-message', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}


// =====================================================
// =================== ADMIN API ======================
// =====================================================

export type UserInfo = {
  id: number;
  login: string;
  name?: string | null;
  inn?: string | null;
  phone?: string | null;
  role?: 'admin' | 'client' | 'agent' | null;
  ownerAgentId?: number | null;
  isDisabled?: boolean | null;
  autoLimitControlEnabled?: boolean | null;
  telegramNotificationsChatId?: string | null;
  telegramAutoPauseEnabled?: boolean | null;
  uniqueProjectNamesEnabled?: boolean | null;
};

export type ClientProfile = {
  name: string;
  inn?: string | null;
  phone?: string | null;
  contact?: string | null;
  internalClientId?: string | null;
  tableUrl?: string | null;
  pixelTableUrl?: string | null;
  workStatus?: ClientWorkStatus | null;
};

export type ClientWorkStatus =
  | 'В работе'
  | 'Ждём оплату'
  | 'Ждём данные'
  | 'На согласовании'
  | 'Пауза по клиенту'
  | 'Неактивен';

export type ClientDataCollectionStatus = 'Нет проектов' | 'Сбор активен' | 'На паузе';
export type ClientFinanceStatus = 'Дожим 1' | 'Дожим 2' | 'Дожим 3' | 'Долг';

export type AdminProject = Project & {
  user: UserInfo;
};

export type AdminProjectListResp = {
  items: AdminProject[];
  total: number;
};

export type AdminProjectUpdate = {
  name: string;
  tag: string;
  status: ProjectMutableStatus;
  deliveryStatus: 'Активна' | 'На модерации' | 'Отключена';
  dataLimit: number;
  regionMode: 'include' | 'exclude';
  regions: string[];
  sites?: string[];
  phones?: string[];
  smsSenderName?: string;
  days: Day[];
};

export type AdminCollectionProjectItem = {
  id: number;
  name: string;
  status: ProjectStatus;
};

export type AdminClientCollectionState = {
  clientId: number;
  dataCollectionStatus: 'Активен' | 'На паузе';
  action: 'pause' | 'resume';
  actionLabel: string;
  pauseCandidates: number;
  resumeCandidates: number;
  actionEnabled: boolean;
  actionDisabledReason?: string | null;
  projectsMutationLocked: boolean;
  projectsMutationLockedAt?: string | null;
  projectsMutationLockedBy?: number | null;
  projectsMutationLockReason?: string | null;
  snapshotProjects: AdminCollectionProjectItem[];
};

export type AdminClientCollectionActionResp = {
  state: AdminClientCollectionState;
  message: string;
  pausedCount: number;
  resumedCount: number;
  skippedCount: number;
  failedCount: number;
  errors: string[];
};

export type OperatorBlockCheckResp = {
  checked: number;
  blocked: number;
  skipped: number;
  blockedProjects: Array<{
    id: number;
    name: string;
    clientName?: string | null;
    providerProjectId?: string | null;
  }>;
  errors: string[];
};

export async function fetchAdminUsers(params?: { includeAgents?: boolean }): Promise<UserInfo[]> {
  const q = new URLSearchParams();
  if (params?.includeAgents) q.set('includeAgents', 'true');
  const qs = q.toString();
  return http<UserInfo[]>(qs ? `/admin/users?${qs}` : '/admin/users');
}

export async function fetchAdminProjects(params?: {
  offset?: number;
  limit?: number;
  q?: string;
  userId?: number;
  sources?: string[];
  collectionSources?: string[];
  fromDate?: string;
  toDate?: string;
  includeDeleted?: boolean;
  projectStatus?: ProjectStatus;
  dailyLimitReached?: boolean;
  isTop?: boolean;
  sortBy?: ProjectSortBy;
  sortDir?: SortDir;
}): Promise<AdminProjectListResp> {
  const q = new URLSearchParams();
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  if (params?.q) q.set('q', params.q);
  if (params?.userId != null) q.set('userId', String(params.userId));
  if (params?.sources && params.sources.length > 0) q.set('sources', params.sources.join(','));
  if (params?.collectionSources && params.collectionSources.length > 0) q.set('collectionSources', params.collectionSources.join(','));
  if (params?.fromDate) q.set('fromDate', params.fromDate);
  if (params?.toDate) q.set('toDate', params.toDate);
  q.set('includeDeleted', params?.includeDeleted ? 'true' : 'false');
  if (params?.projectStatus) q.set('projectStatus', params.projectStatus);
  if (params?.dailyLimitReached) q.set('dailyLimitReached', 'true');
  if (params?.isTop) q.set('isTop', 'true');
  if (params?.sortBy) q.set('sortBy', params.sortBy);
  if (params?.sortDir) q.set('sortDir', params.sortDir);
  const qs = q.toString();
  return http<AdminProjectListResp>(`/admin/projects${qs ? `?${qs}` : ''}`);
}

export async function fetchAdminProject(id: number): Promise<AdminProject> {
  return http<AdminProject>(`/admin/projects/${id}`);
}

export type UpdateAdminProjectResp = { project: AdminProject; warning?: string | null };

export async function updateAdminProject(id: number, payload: AdminProjectUpdate): Promise<UpdateAdminProjectResp> {
  return http<UpdateAdminProjectResp>(`/admin/projects/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function setAdminProjectTop(id: number, isTop: boolean): Promise<AdminProject> {
  return http<AdminProject>(`/admin/projects/${id}/top`, {
    method: 'PATCH',
    body: JSON.stringify({ isTop }),
  });
}

export async function deleteAdminProject(id: number): Promise<void> {
  await http(`/admin/projects/${id}`, { method: 'DELETE' });
}

export async function runAdminOperatorBlockCheck(): Promise<OperatorBlockCheckResp> {
  return http<OperatorBlockCheckResp>('/admin/operator-block-check/run', { method: 'POST' });
}

export type DashboardRiskLevel = 'warning' | 'risk' | 'critical' | 'debt';

export type AdminDashboardSummary = {
  clients: number;
  projects: number;
  activeProjects: number;
  pausedProjects: number;
  operatorBlockedProjects: number;
  totalRemaining: number;
  leadsPeriod: number;
  leadsToday: number;
  leadsYesterday: number;
  leads7Days: number;
  leads30Days: number;
  averageWorkday7: number;
  averageWorkday3: number;
  unlinkedLeads: number;
  operationErrors: number;
};

export type AdminDashboardAttentionClient = {
  clientId: number;
  clientName: string;
  clientLogin: string;
  ownerType: 'admin' | 'agent';
  ownerName?: string | null;
  remaining: number;
  tariffAmount?: number | null;
  signal1: number;
  signal2: number;
  signal3?: number | null;
  level: DashboardRiskLevel;
  activeProjects: number;
  dailySpend: number;
  lastTariffAt?: string | null;
};

export type AdminDashboardAttentionProject = {
  projectId: number;
  projectName: string;
  clientId?: number | null;
  clientName?: string | null;
  source: string;
  detectedAt?: string | null;
};

export type AdminDashboardUnlinkedLeads = {
  total: number;
  ambiguous: number;
  notFound: number;
  unknown: number;
};

export type AdminDashboardOperationErrors = {
  total: number;
  items: Array<{
    id: number;
    clientId: number;
    clientName: string;
    projectId?: number | null;
    projectName?: string | null;
    operation?: string | null;
    errorMessage?: string | null;
    createdAt?: string | null;
  }>;
};

export type AdminDashboardAttention = {
  criticalClients: AdminDashboardAttentionClient[];
  riskClients: AdminDashboardAttentionClient[];
  warningClients: AdminDashboardAttentionClient[];
  operatorBlockedProjects: AdminDashboardAttentionProject[];
  unlinkedLeads: AdminDashboardUnlinkedLeads;
  operationErrors: AdminDashboardOperationErrors;
};

export type AdminDashboardBreakdownItem = {
  key: string;
  label: string;
  value: number;
};

export type AdminDashboardSeriesPoint = {
  date: string;
  value: number;
};

export type AdminDashboardCharts = {
  leadsDaily: AdminDashboardSeriesPoint[];
  sourceBreakdown: AdminDashboardBreakdownItem[];
  projectStatuses: AdminDashboardBreakdownItem[];
};

export type ProjectChart = {
  projectId: number;
  projectName: string;
  fromDate: string;
  toDate: string;
  total: number;
  averageDaily: number;
  leadsDaily: AdminDashboardSeriesPoint[];
  sourceBreakdown: AdminDashboardBreakdownItem[];
};

export type AdminDashboardRankingItem = {
  clientId: number;
  clientName: string;
  ownerName?: string | null;
  value: number;
  activeProjects: number;
};

export type AdminDashboard = {
  summary: AdminDashboardSummary;
  attention: AdminDashboardAttention;
  charts: AdminDashboardCharts;
  rankings: {
    topClientsByLeads: AdminDashboardRankingItem[];
    topClientsByActiveProjects: AdminDashboardRankingItem[];
  };
};

export async function fetchAdminDashboard(params: {
  fromDate: string;
  toDate: string;
  clientId?: number | null;
  sources?: string[];
  includeAgentClients?: boolean;
}): Promise<AdminDashboard> {
  const q = new URLSearchParams({
    fromDate: params.fromDate,
    toDate: params.toDate,
    includeAgentClients: params.includeAgentClients === false ? 'false' : 'true',
  });
  if (params.clientId != null) q.set('clientId', String(params.clientId));
  if (params.sources && params.sources.length > 0) q.set('sources', params.sources.join(','));
  return http<AdminDashboard>(`/admin/dashboard?${q.toString()}`);
}

export type ClientDashboardSummary = {
  leadsPeriod: number;
  leadsToday: number;
  leads7Days: number;
  leads30Days: number;
  remaining: number;
  activeProjects: number;
  pausedProjects: number;
  operatorBlockedProjects: number;
};

export type ClientDashboardBalance = {
  remaining: number;
  averageDailySpend: number;
  averageDailySpendBasis?: '7d' | '30d' | null;
  estimatedDaysLeft?: number | null;
};

export type ClientDashboardAttentionProject = {
  projectId: number;
  projectName: string;
  status: ProjectStatus;
  source: string;
  reason: 'operator_blocked' | 'paused' | 'no_data_7d';
  reasonLabel: string;
};

export type ClientDashboardProjectRankingItem = {
  projectId: number;
  projectName: string;
  status: ProjectStatus;
  source: string;
  value: number;
};

export type ClientDashboard = {
  summary: ClientDashboardSummary;
  balance: ClientDashboardBalance;
  charts: {
    leadsDaily: AdminDashboardSeriesPoint[];
    projectStatuses: AdminDashboardBreakdownItem[];
  };
  attention: {
    projects: ClientDashboardAttentionProject[];
  };
  rankings: {
    topProjectsByLeads: ClientDashboardProjectRankingItem[];
  };
  recentEvents: ActivityEvent[];
};

export async function fetchClientDashboard(params: {
  fromDate: string;
  toDate: string;
  sources?: string[];
}): Promise<ClientDashboard> {
  const q = new URLSearchParams({
    fromDate: params.fromDate,
    toDate: params.toDate,
  });
  if (params.sources && params.sources.length > 0) q.set('sources', params.sources.join(','));
  return http<ClientDashboard>(`/dashboard?${q.toString()}`);
}

export async function fetchProjectChart(
  projectId: number,
  params: { fromDate: string; toDate: string },
): Promise<ProjectChart> {
  const q = new URLSearchParams({
    fromDate: params.fromDate,
    toDate: params.toDate,
  });
  return http<ProjectChart>(`/projects/${projectId}/chart?${q.toString()}`);
}

export async function fetchAdminProjectChart(
  projectId: number,
  params: { fromDate: string; toDate: string },
): Promise<ProjectChart> {
  const q = new URLSearchParams({
    fromDate: params.fromDate,
    toDate: params.toDate,
  });
  return http<ProjectChart>(`/admin/projects/${projectId}/chart?${q.toString()}`);
}

export async function fetchAdminClientCollectionState(clientId: number): Promise<AdminClientCollectionState> {
  return http<AdminClientCollectionState>(`/admin/clients/${clientId}/collection-state`);
}

export async function pauseAdminClientProjects(clientId: number): Promise<AdminClientCollectionActionResp> {
  return http<AdminClientCollectionActionResp>(`/admin/clients/${clientId}/collection/pause`, {
    method: 'POST',
  });
}

export async function resumeAdminClientProjects(clientId: number): Promise<AdminClientCollectionActionResp> {
  return http<AdminClientCollectionActionResp>(`/admin/clients/${clientId}/collection/resume`, {
    method: 'POST',
  });
}

// -------- Админские лиды --------
export type AdminLead = {
  ext_id: string;
  lk_id: string;
  project_id?: number | null;
  created_at: string; // дата в ЛК (момент записи в БД, = imported_at)
  imported_at: string; // время попадания в БД
  phone: string;
  utm_campaign?: string | null;
  source?: string | null;
  lead_source?: 'provider' | 'pixel';
  pixel_url?: string | null;
  collection_source?: string | null;
  project_name?: string | null;
  user: UserInfo;
};

export type AdminLeadsListResp = {
  items: AdminLead[];
  total: number;
};

export async function fetchAdminLeads(params: {
  fromDate: string;
  toDate: string;
  userId?: number;
  projectIds?: number[];
  sources?: string[];
  collectionSources?: string[];
  unlinked?: boolean;
  offset?: number;
  limit?: number;
}): Promise<AdminLeadsListResp> {
  const q = new URLSearchParams({
    fromDate: params.fromDate,
    toDate: params.toDate,
  });
  if (params.userId != null) q.set('userId', String(params.userId));
  if (params.projectIds && params.projectIds.length > 0) {
    q.set('projectIds', params.projectIds.join(','));
  }
  if (params.sources && params.sources.length > 0) {
    q.set('sources', params.sources.join(','));
  }
  if (params.collectionSources && params.collectionSources.length > 0) {
    q.set('collectionSources', params.collectionSources.join(','));
  }
  if (params.unlinked) q.set('unlinked', 'true');
  if (params.offset != null) q.set('offset', String(params.offset));
  if (params.limit != null) q.set('limit', String(params.limit));
  return http<AdminLeadsListResp>(`/admin/leads?${q.toString()}`);
}

export type AdminProviderLeadsImportPreviewSample = {
  xlsxRowNumber?: number | null;
  vid?: string | null;
  projectName?: string | null;
  phone?: string | null;
  subdomain?: string | null;
  note: string;
};

export type AdminProviderLeadsImportPreviewResp = {
  previewId: string;
  fileName: string;
  totalRows: number;
  validRows: number;
  rowsWithErrors: number;
  duplicatesInFile: number;
  duplicatesInDb: number;
  newRows: number;
  readyToImport: number;
  matchedProjects: number;
  notFoundProjects: number;
  ambiguousProjects: number;
  errorsBreakdown: Record<string, number>;
  samples: Record<string, AdminProviderLeadsImportPreviewSample[]>;
};

export type AdminProviderLeadsImportCommitResp = {
  previewId: string;
  fileName: string;
  insertedRows: number;
  skippedDuplicatesInDb: number;
};

export async function previewAdminProviderLeadsImport(file: File): Promise<AdminProviderLeadsImportPreviewResp> {
  const formData = new FormData();
  formData.append('file', file);
  return httpForm<AdminProviderLeadsImportPreviewResp>('/admin/provider-leads-import/preview', formData);
}

export async function commitAdminProviderLeadsImport(previewId: string): Promise<AdminProviderLeadsImportCommitResp> {
  return http<AdminProviderLeadsImportCommitResp>('/admin/provider-leads-import/commit', {
    method: 'POST',
    body: JSON.stringify({ previewId }),
  });
}

// -------- Админский черный список --------
export type AdminBlacklistPhone = BlacklistPhone & {
  user: UserInfo;
};

export type AdminBlacklistListResp = {
  items: AdminBlacklistPhone[];
  total: number;
};

export async function fetchAdminBlacklist(params?: {
  offset?: number;
  limit?: number;
  q?: string;
  userId?: number;
}): Promise<AdminBlacklistListResp> {
  const q = new URLSearchParams();
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  if (params?.q) q.set('q', params.q);
  if (params?.userId != null) q.set('userId', String(params.userId));
  const qs = q.toString();
  return http<AdminBlacklistListResp>(`/admin/blacklist${qs ? `?${qs}` : ''}`);
}

// -------- Админские отчёты --------
export type AdminReportItem = ReportItem & {
  user: UserInfo;
  client?: UserInfo | null;
};

export type AdminReportsListResp = {
  items: AdminReportItem[];
  total: number;
};

export async function fetchAdminReports(params?: {
  offset?: number;
  limit?: number;
  fromDate?: string;
  toDate?: string;
  clientId?: number | null;
}): Promise<AdminReportsListResp> {
  const q = new URLSearchParams();
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  if (params?.fromDate) q.set('fromDate', params.fromDate);
  if (params?.toDate) q.set('toDate', params.toDate);
  if (params?.clientId != null) q.set('clientId', String(params.clientId));
  const qs = q.toString();
  return http<AdminReportsListResp>(`/admin/reports${qs ? `?${qs}` : ''}`);
}

// -------- Админские изменения клиентов --------
export type AdminChangeStatus = 'pending' | 'done';

export type AdminChangeAction = 'create' | 'update' | 'delete' | 'blacklist_add' | 'blacklist_delete';

export type ProjectDuplicateDiagnosticMatch = {
  projectId: number;
  projectName: string;
  clientId?: number | null;
  clientName?: string | null;
  clientLogin?: string | null;
};

export type ProjectDuplicateDiagnostics = {
  kind: 'sites' | 'phones';
  reason: 'provider_missing_items' | 'provider_duplicate_error' | string;
  summary?: string | null;
  items: Array<{
    value: string;
    matches: ProjectDuplicateDiagnosticMatch[];
    externalProviderOnly?: boolean;
  }>;
};

export type ProjectChangeCardData = {
  id?: number;
  eventId?: string;
  source?: 'audit' | 'project_operation';
  sourceId?: number;
  projectId?: number | null;
  projectName?: string | null;
  sources?: string[]; // список источников батча (для созданий)
  // Для агрегированного "создания" (batchId): лимит по каждому источнику (B1..B4).
  // Это поле формируется на фронте (из projectSnapshot), бэк его не обязан присылать.
  sourceLimits?: Record<string, number>;
  createdAt: string;
  action: AdminChangeAction;
  description: string;
  status?: AdminChangeStatus;
  outcome?: 'success' | 'failed';
  errorMessage?: string | null;
  projectSnapshot?: Record<string, unknown> | null;
  beforeSnapshot?: Record<string, unknown> | null;
  changedFields?: string[] | null;
  actor?: UserInfo | null;
  actorMode?: 'client' | 'admin' | 'admin_impersonation' | null;
};

export type AdminChange = ProjectChangeCardData & {
  id: number;
  batchId?: string | null;
  action: AdminChangeAction;
};

export type AdminClientChangesOut = {
  user: UserInfo;
  items: AdminChange[];
};

export type AdminClientChangesSummaryItem = {
  user: UserInfo;
  pendingChanges: number;
  pendingCreates: number;
  pendingBlacklistAdds: number;
  pendingBlacklistDeletes: number;
  pendingTotal: number;
};

export type AdminClientChangesSummaryListOut = {
  items: AdminClientChangesSummaryItem[];
};

export async function fetchAdminChangesSummary(params?: { actions?: AdminChangeAction[] }): Promise<AdminClientChangesSummaryListOut> {
  const q = new URLSearchParams();
  if (params?.actions && params.actions.length) q.set('actions', params.actions.join(','));
  const qs = q.toString();
  return http<AdminClientChangesSummaryListOut>(`/admin/changes/summary${qs ? `?${qs}` : ''}`);
}

export async function fetchAdminClientChanges(clientId: number, params?: { actions?: AdminChangeAction[] }): Promise<AdminClientChangesOut> {
  const q = new URLSearchParams();
  if (params?.actions && params.actions.length) q.set('actions', params.actions.join(','));
  const qs = q.toString();
  return http<AdminClientChangesOut>(`/admin/changes/${clientId}${qs ? `?${qs}` : ''}`);
}

export async function resolveAdminChange(changeId: number): Promise<{ ok: boolean; processed?: number; batch?: string }> {
  return http<{ ok: boolean; processed?: number; batch?: string }>(`/admin/changes/${changeId}/resolve`, { method: 'POST' });
}

export async function fetchAdminClientChangesSummary(): Promise<AdminClientChangesSummaryListOut> {
  return http<AdminClientChangesSummaryListOut>('/admin/changes/summary');
}

// -------- Сводка по клиентам --------
export type AdminClientSummaryItem = {
  user: UserInfo;
  profile?: ClientProfile | null;
  ownerType: 'admin' | 'agent';
  ownerUser?: UserInfo | null;
  projectCount: number;
  totalLimit: number;
  usedTotal: number;
  usedPeriod: number;
  usedPeriodBySource?: Record<string, number>;
  averageWorkday7?: number;
  averageWorkday3?: number;
  averageWorkday7BySource?: Record<string, number>;
  averageWorkday3BySource?: Record<string, number>;
  leadsDaily30BySource?: Record<string, AdminDashboardSeriesPoint[]>;
  remaining: number;
  pendingChanges: number;
  pendingCreates: number;
  numbersCredited?: number | null;
  numbersDebited?: number | null;
  numbersBalance?: number | null;
  numbersUsed?: number | null;
  numbersUsedPeriod?: number | null;
  tariffAmount?: number | null;
  autoLimitControlEnabled: boolean;
  dataCollectionStatus?: ClientDataCollectionStatus;
  financeStatus?: ClientFinanceStatus | null;
  workStatus?: ClientWorkStatus;
};

export type AdminClientsSummaryOut = {
  items: AdminClientSummaryItem[];
  totals: {
    clients: number;
    projects: number;
    totalLimit: number;
    usedTotal: number;
    usedPeriod: number;
    remaining: number;
  };
};

export type AdminClientCreatePayload = {
  name: string;
  inn?: string;
  phone?: string;
  contact?: string;
  login?: string;
  password?: string;
  autoLimitControlEnabled?: boolean;
  telegramNotificationsChatId?: string;
  telegramAutoPauseEnabled?: boolean;
  uniqueProjectNamesEnabled?: boolean;
  internalClientId?: string;
  tableUrl?: string;
  pixelTableUrl?: string;
  ownerAgentId?: number | null;
};

export type AdminClientCreateResp = {
  user: UserInfo;
  profile: ClientProfile;
  login: string;
  password: string;
};

export type AdminClientUpdatePayload = {
  name?: string;
  inn?: string;
  phone?: string;
  contact?: string;
  login?: string;
  password?: string;
  autoLimitControlEnabled?: boolean;
  telegramNotificationsChatId?: string;
  telegramAutoPauseEnabled?: boolean;
  uniqueProjectNamesEnabled?: boolean;
  internalClientId?: string;
  tableUrl?: string;
  pixelTableUrl?: string;
  ownerAgentId?: number | null;
};

export type AdminClientUpdateResp = {
  user: UserInfo;
  profile: ClientProfile;
  login: string;
  password?: string | null;
  telegramTestStatus?: 'queued' | 'failed' | null;
  telegramTestNotificationId?: number | null;
};

export async function fetchAdminClientsSummary(params: { fromDate: string; toDate: string }): Promise<AdminClientsSummaryOut> {
  const q = new URLSearchParams({ fromDate: params.fromDate, toDate: params.toDate });
  return http<AdminClientsSummaryOut>(`/admin/clients/summary?${q.toString()}`);
}

export async function createAdminClient(payload: AdminClientCreatePayload): Promise<AdminClientCreateResp> {
  return http<AdminClientCreateResp>('/admin/clients', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updateAdminClient(clientId: number, payload: AdminClientUpdatePayload): Promise<AdminClientUpdateResp> {
  return http<AdminClientUpdateResp>(`/admin/clients/${clientId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function updateAdminClientWorkStatus(
  clientId: number,
  workStatus: ClientWorkStatus,
): Promise<{ clientId: number; workStatus: ClientWorkStatus }> {
  return http<{ clientId: number; workStatus: ClientWorkStatus }>(`/admin/clients/${clientId}/work-status`, {
    method: 'PATCH',
    body: JSON.stringify({ workStatus }),
  });
}

export type ClientOwnerTransferResp = {
  client: UserInfo;
  ownerType: 'admin' | 'agent';
  ownerUser?: UserInfo | null;
  transferredBalance: number;
};

export async function transferAdminClientOwner(
  clientId: number,
  payload: { ownerType: 'admin' | 'agent'; agentId?: number | null },
): Promise<ClientOwnerTransferResp> {
  return http<ClientOwnerTransferResp>(`/admin/clients/${clientId}/owner`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export type AdminAgentSummaryItem = {
  user: UserInfo;
  clientCount: number;
  credited: number;
  debited: number;
  balance: number;
  createdAt: string;
};

export type AdminAgentsListResp = {
  items: AdminAgentSummaryItem[];
  total: number;
};

export type AdminAgentCreatePayload = {
  name: string;
  inn: string;
  phone: string;
  login?: string;
  password?: string;
};

export type AdminAgentCreateResp = {
  user: UserInfo;
  login: string;
  password: string;
};

export type AdminAgentUpdatePayload = {
  name?: string;
  inn?: string;
  phone?: string;
  login?: string;
  password?: string;
  isDisabled?: boolean;
};

export type AdminAgentUpdateResp = {
  user: UserInfo;
  login: string;
  password?: string | null;
};

export async function fetchAdminAgents(): Promise<AdminAgentsListResp> {
  return http<AdminAgentsListResp>('/admin/agents');
}

export async function createAdminAgent(payload: AdminAgentCreatePayload): Promise<AdminAgentCreateResp> {
  return http<AdminAgentCreateResp>('/admin/agents', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updateAdminAgent(agentId: number, payload: AdminAgentUpdatePayload): Promise<AdminAgentUpdateResp> {
  return http<AdminAgentUpdateResp>(`/admin/agents/${agentId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function fetchClientBalanceSummary(
  params?: { fromDate?: string; toDate?: string },
): Promise<ClientBalanceSummary> {
  const q = new URLSearchParams();
  if (params?.fromDate) q.set('fromDate', params.fromDate);
  if (params?.toDate) q.set('toDate', params.toDate);
  const suffix = q.toString();
  return http<ClientBalanceSummary>(suffix ? `/balance?${suffix}` : '/balance');
}

export async function fetchClientBalanceOps(
  params?: { fromDate?: string; toDate?: string; offset?: number; limit?: number },
): Promise<ClientBalanceOpsList> {
  const q = new URLSearchParams();
  if (params?.fromDate) q.set('fromDate', params.fromDate);
  if (params?.toDate) q.set('toDate', params.toDate);
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  const suffix = q.toString();
  return http<ClientBalanceOpsList>(suffix ? `/balance/ops?${suffix}` : '/balance/ops');
}

// -------- Баланс по номерам --------
export type BalanceOpType = 'credit' | 'debit';

export type BalanceOperation = {
  id: number;
  clientId: number;
  amount: number;
  type: BalanceOpType;
  comment?: string | null;
  createdAt: string;
  createdBy: UserInfo;
};

export type ClientBalanceSummary = {
  clientId: number;
  credited: number;
  debited: number;
  manualBalance: number;
  usedTotal: number;
  usedPeriod: number;
  remaining: number;
  debt: boolean;
  periodFrom?: string | null;
  periodTo?: string | null;
};

export type ClientBalanceOpsList = {
  items: BalanceOperation[];
  total: number;
};

export async function fetchAdminClientBalanceSummary(
  clientId: number,
  params?: { fromDate?: string; toDate?: string },
): Promise<ClientBalanceSummary> {
  const q = new URLSearchParams();
  if (params?.fromDate) q.set('fromDate', params.fromDate);
  if (params?.toDate) q.set('toDate', params.toDate);
  const suffix = q.toString();
  return http<ClientBalanceSummary>(suffix ? `/admin/clients/${clientId}/balance?${suffix}` : `/admin/clients/${clientId}/balance`);
}

export async function fetchAdminClientBalanceOps(
  clientId: number,
  params?: { fromDate?: string; toDate?: string; offset?: number; limit?: number },
): Promise<ClientBalanceOpsList> {
  const q = new URLSearchParams();
  if (params?.fromDate) q.set('fromDate', params.fromDate);
  if (params?.toDate) q.set('toDate', params.toDate);
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  const suffix = q.toString();
  return http<ClientBalanceOpsList>(suffix ? `/admin/clients/${clientId}/balance/ops?${suffix}` : `/admin/clients/${clientId}/balance/ops`);
}

export async function createAdminClientBalanceOp(
  clientId: number,
  payload: { amount: number; type: BalanceOpType; comment?: string },
): Promise<BalanceOperation> {
  return http<BalanceOperation>(`/admin/clients/${clientId}/balance/ops`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function fetchAdminAgentBalanceSummary(
  agentId: number,
  params?: { fromDate?: string; toDate?: string },
): Promise<ClientBalanceSummary> {
  const q = new URLSearchParams();
  if (params?.fromDate) q.set('fromDate', params.fromDate);
  if (params?.toDate) q.set('toDate', params.toDate);
  const suffix = q.toString();
  return http<ClientBalanceSummary>(suffix ? `/admin/agents/${agentId}/balance?${suffix}` : `/admin/agents/${agentId}/balance`);
}

export async function fetchAdminAgentBalanceOps(
  agentId: number,
  params?: { fromDate?: string; toDate?: string; offset?: number; limit?: number },
): Promise<ClientBalanceOpsList> {
  const q = new URLSearchParams();
  if (params?.fromDate) q.set('fromDate', params.fromDate);
  if (params?.toDate) q.set('toDate', params.toDate);
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  const suffix = q.toString();
  return http<ClientBalanceOpsList>(suffix ? `/admin/agents/${agentId}/balance/ops?${suffix}` : `/admin/agents/${agentId}/balance/ops`);
}

export async function createAdminAgentBalanceOp(
  agentId: number,
  payload: { amount: number; type: BalanceOpType; comment?: string },
): Promise<BalanceOperation> {
  return http<BalanceOperation>(`/admin/agents/${agentId}/balance/ops`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// -------- Тарифы клиента --------
export type ClientTariff = {
  id: number;
  clientId: number;
  baseAmount: number;
  currentAmount: number;
  comment?: string | null;
  signal1?: number | null;
  signal2?: number | null;
  signal3?: number | null;
  createdAt: string;
  updatedAt: string;
  createdBy: UserInfo;
};

export type ClientTariffList = {
  items: ClientTariff[];
  total: number;
};

export type ClientTariffOperation = {
  id: number;
  tariffId: number;
  amount: number;
  type: BalanceOpType;
  comment: string;
  createdAt: string;
  createdBy: UserInfo;
};

export type ClientTariffOperationList = {
  items: ClientTariffOperation[];
  total: number;
};

export async function fetchAdminClientTariffs(
  clientId: number,
  params?: { offset?: number; limit?: number },
): Promise<ClientTariffList> {
  const q = new URLSearchParams();
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  const suffix = q.toString();
  return http<ClientTariffList>(suffix ? `/admin/clients/${clientId}/tariffs?${suffix}` : `/admin/clients/${clientId}/tariffs`);
}

export async function createAdminClientTariff(
  clientId: number,
  payload: { amount: number; comment?: string; signal1: number; signal2: number; signal3?: number | null },
): Promise<ClientTariff> {
  return http<ClientTariff>(`/admin/clients/${clientId}/tariffs`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updateAdminTariff(
  tariffId: number,
  payload: { amount: number; comment?: string; signal1: number; signal2: number; signal3?: number | null },
): Promise<ClientTariff> {
  return http<ClientTariff>(`/admin/tariffs/${tariffId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function fetchAdminTariff(
  tariffId: number,
): Promise<ClientTariff> {
  return http<ClientTariff>(`/admin/tariffs/${tariffId}`);
}

export async function fetchAdminTariffOps(
  tariffId: number,
  params?: { offset?: number; limit?: number },
): Promise<ClientTariffOperationList> {
  const q = new URLSearchParams();
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  const suffix = q.toString();
  return http<ClientTariffOperationList>(suffix ? `/admin/tariffs/${tariffId}/ops?${suffix}` : `/admin/tariffs/${tariffId}/ops`);
}

export async function createAdminTariffOp(
  tariffId: number,
  payload: { amount: number; type: BalanceOpType; comment: string },
): Promise<ClientTariffOperation> {
  return http<ClientTariffOperation>(`/admin/tariffs/${tariffId}/ops`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// -------- История проекта (админ) --------
export type AdminProjectHistoryItem = {
  id: number;
  eventId: string;
  action: 'create' | 'update' | 'delete';
  createdAt: string;
  description: string;
  outcome?: 'success' | 'failed';
  errorMessage?: string | null;
  user?: UserInfo;
  actorMode?: 'client' | 'admin' | 'admin_impersonation' | null;
  status: AdminChangeStatus;
};

export type AdminProjectHistoryResp = {
  items: AdminProjectHistoryItem[];
  total: number;
};

export async function fetchAdminProjectHistory(projectId: number, params: { fromDate: string; toDate: string; limit?: number; userId?: number; status?: 'pending' | 'done' | 'all'; }): Promise<AdminProjectHistoryResp> {
  const q = new URLSearchParams({ fromDate: params.fromDate, toDate: params.toDate });
  if (params.limit != null) q.set('limit', String(params.limit));
  if (params.userId != null) q.set('userId', String(params.userId));
  if (params.status) q.set('status', params.status);
  return http<AdminProjectHistoryResp>(`/admin/projects/${projectId}/history?${q.toString()}`);
}

// -------- Создание отчёта админом --------
export async function createAdminReport(payload: { fromDate: string; toDate: string; projectIds?: number[]; format: 'csv' | 'xlsx'; clientId?: number | null; }): Promise<AdminReportItem> {
  return http<AdminReportItem>('/admin/reports', {
    method: 'POST',
    body: JSON.stringify({
      fromDate: payload.fromDate,
      toDate: payload.toDate,
      projectIds: payload.projectIds,
      format: payload.format,
      clientId: payload.clientId,
    }),
  });
}
