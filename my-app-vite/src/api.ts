// Файл: src/api.ts
// Назначение: HTTP-клиент фронтенда для работы с бэкендом (projects, client-errors).
import type { Project, ProjectStatus } from './types/project';

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
export type CollectionSource = 'Сайты'|'Звонки'|'СМС'|'Ретросайты'|'Ретрозвонки'|'Пересечение';

export type CreateProjectItem = {
  name: string;
  tag: string;
  collectionSource: CollectionSource;
  dataSourceCode: 'B1'|'B2'|'B3'|'B4';
  dataLimit: number;
  status: 'Активен'|'На паузе'|'Удалён';
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
  status: 'Активен'|'На паузе'|'Удалён';
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
      }
    } catch {
      errorDetail = null;
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

export async function fetchProjects(params?: {
  offset?: number;
  limit?: number;
  q?: string;
  fromDate?: string;
  toDate?: string;
  includeDeleted?: boolean;
  projectStatus?: ProjectStatus;
}): Promise<ProjectListResp> {
  const q = new URLSearchParams();
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  if (params?.q) q.set('q', params.q);
  if (params?.fromDate) q.set('fromDate', params.fromDate);
  if (params?.toDate) q.set('toDate', params.toDate);
  if (params?.includeDeleted) q.set('includeDeleted', 'true');
  if (params?.projectStatus) q.set('projectStatus', params.projectStatus);
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
  isDisabled: boolean;
  projectsMutationLocked: boolean;
  projectsMutationLockedAt?: string | null;
  projectsMutationLockedBy?: number | null;
  projectsMutationLockReason?: string | null;
  autoLimitControlEnabled: boolean;
  telegramNotificationsChatId?: string | null;
  telegramAutoPauseEnabled: boolean;
  uniqueProjectNamesEnabled: boolean;
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
  project_id?: number | null;
  created_at: string; // ISO string (время из источника)
  imported_at: string; // ISO string (время попадания в БД)
  phone: string;
  utm_campaign?: string | null;
  source?: string | null;
};

export type LeadsListResp = { items: Lead[]; total: number };

export async function fetchLeads(params: { projectIds?: number[]; sources?: string[]; fromDate: string; toDate: string; offset?: number; limit?: number; }): Promise<LeadsListResp> {
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
  if (params.offset != null) q.set('offset', String(params.offset));
  if (params.limit != null) q.set('limit', String(params.limit));
  return http<LeadsListResp>(`/leads?${q.toString()}`);
}


// -------- История изменений проектов --------
export type ProjectHistoryItem = {
  id: number;
  action: 'create' | 'update' | 'delete';
  createdAt: string;      // 'YYYY-MM-DD HH:MM:SS'
  description: string;    // краткое текстовое описание изменения
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
  actor?: UserInfo | null; // кто выполнил действие
  description: string;     // краткое описание
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

export function buildLeadsExportUrl(params: {
  projectIds?: number[];
  sources?: string[];
  fromDate: string;
  toDate: string;
  format: 'csv'|'xlsx';
  source?: 'leads' | 'reports';
  clientId?: number;
}): string {
  const q = new URLSearchParams({ fromDate: params.fromDate, toDate: params.toDate, format: params.format });
  if (params.projectIds && params.projectIds.length > 0) q.set('projectIds', params.projectIds.join(','));
  if (params.sources && params.sources.length > 0) q.set('sources', params.sources.join(','));
  if (params.source) q.set('source', params.source);
  if (params.clientId != null) q.set('clientId', String(params.clientId));

  // Токен теперь передается через cookies, а не в URL (для безопасности)
  // Сервер автоматически прочитает токен из cookies при скачивании файла
  return `${API_BASE}/leads/export?${q.toString()}`;
}

export async function downloadLeadsExport(params: {
  projectIds?: number[];
  sources?: string[];
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
  inn: string;
  phone: string;
  contact?: string | null;
};

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
  status: 'Активен' | 'На паузе' | 'Удалён';
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
  status: 'Активен' | 'На паузе' | 'Удалён';
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
  fromDate?: string;
  toDate?: string;
  includeDeleted?: boolean;
  projectStatus?: ProjectStatus;
}): Promise<AdminProjectListResp> {
  const q = new URLSearchParams();
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  if (params?.q) q.set('q', params.q);
  if (params?.userId != null) q.set('userId', String(params.userId));
  if (params?.fromDate) q.set('fromDate', params.fromDate);
  if (params?.toDate) q.set('toDate', params.toDate);
  q.set('includeDeleted', params?.includeDeleted ? 'true' : 'false');
  if (params?.projectStatus) q.set('projectStatus', params.projectStatus);
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
  project_id?: number | null;
  created_at: string;
  imported_at: string;
  phone: string;
  utm_campaign?: string | null;
  source?: string | null;
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

export type AdminChange = {
  id: number;
  projectId?: number | null;
  projectName?: string | null;
  batchId?: string | null;
  sources?: string[]; // список источников батча (для созданий)
  // Для агрегированного "создания" (batchId): лимит по каждому источнику (B1..B4).
  // Это поле формируется на фронте (из projectSnapshot), бэк его не обязан присылать.
  sourceLimits?: Record<string, number>;
  createdAt: string;
  action: AdminChangeAction;
  description: string;
  status?: AdminChangeStatus;
  projectSnapshot?: Record<string, unknown> | null;
  beforeSnapshot?: Record<string, unknown> | null;
  changedFields?: string[] | null;
  actor?: UserInfo | null;
  actorMode?: 'client' | 'admin' | 'admin_impersonation' | null;
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
  inn: string;
  phone: string;
  contact?: string;
  login?: string;
  password?: string;
  autoLimitControlEnabled?: boolean;
  telegramNotificationsChatId?: string;
  telegramAutoPauseEnabled?: boolean;
  uniqueProjectNamesEnabled?: boolean;
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
  ownerAgentId?: number | null;
};

export type AdminClientUpdateResp = {
  user: UserInfo;
  profile: ClientProfile;
  login: string;
  password?: string | null;
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
  payload: { amount: number; comment?: string },
): Promise<ClientTariff> {
  return http<ClientTariff>(`/admin/clients/${clientId}/tariffs`, {
    method: 'POST',
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
  action: 'create' | 'update' | 'delete';
  createdAt: string;
  description: string;
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
export async function createAdminReport(payload: { fromDate: string; toDate: string; projectIds?: number[]; format: 'csv' | 'xlsx'; clientId: number; }): Promise<AdminReportItem> {
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
