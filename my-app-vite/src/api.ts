// Файл: src/api.ts
// Назначение: HTTP-клиент фронтенда для работы с бэкендом (projects, client-errors).
import type { Project } from './types/project';

// Базовый URL для API:
// - в проде берётся из Vite-переменной окружения VITE_API_BASE (например, "/api")
// - дополнительно, если страница открыта на leadrecordwh.ru — форсируем "/api"
// - локально по умолчанию используется http://localhost:8000
// Важно использовать именно import.meta.env, чтобы Vite смог подставить значение на этапе сборки.
const RUNTIME_HOST =
  typeof window !== 'undefined' ? window.location.hostname : undefined;

const API_BASE =
  // 1) Явное значение из .env/.env.production
  (import.meta.env as any).VITE_API_BASE ||
  // 2) Если мы на прод-домене — всегда ходим через /api (через Nginx)
  (RUNTIME_HOST === 'leadrecordwh.ru' ? '/api' : undefined) ||
  // 3) Фолбэк для локальной разработки
  'http://localhost:8000';

export type Day = 'Пн'|'Вт'|'Ср'|'Чт'|'Пт'|'Сб'|'Вс';
export type CollectionSource = 'Сайты'|'Звонки'|'СМС'|'Ретросайты'|'Ретрозвонки'|'Пересечение';

export type CreateProjectItem = {
  name: string;
  tag: string;
  collectionSource: CollectionSource;
  dataSourceCode: 'B1'|'B2'|'B3'|'B4';
  dataLimit: number;
  status: 'Активен'|'На паузе';
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
  status: 'Активен'|'На паузе';
  dataLimit: number;
  regionMode: 'include'|'exclude';
  regions: string[];
  sites?: string[];
  phones?: string[];
  smsSenderName?: string;
  days: Day[];
};

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
  } catch (e) {
    // Сетевая ошибка (нет интернета, сервер недоступен)
    const err = new Error('Нет соединения с сервером. Проверьте подключение к интернету.') as any;
    err.status = 0;
    err.isNetworkError = true;
    throw err;
  }

  if (!res.ok) {
    let errorMessage = res.statusText;
    let errorDetail: string | null = null;
    
    try {
      const text = await res.text();
      if (text) {
        try {
          // Пытаемся распарсить JSON ответ
          const json = JSON.parse(text);
          errorDetail = json.detail || json.message || text;
        } catch {
          // Если не JSON, используем текст как есть
          errorDetail = text;
        }
      }
    } catch {
      // Если не удалось прочитать ответ
      errorDetail = null;
    }

    if (res.status === 401) {
      // токен недействителен — очищаем и кидаем 401
      try { localStorage.removeItem('access_token'); } catch {}
    }
    const err = new Error(errorDetail || errorMessage) as any;
    err.status = res.status;
    err.errorDetail = errorDetail;
    throw err;
  }
  return res.json();
}

export type ProjectListResp = { items: Project[]; total: number };

export async function fetchProjects(params?: { offset?: number; limit?: number; q?: string }): Promise<ProjectListResp> {
  const q = new URLSearchParams();
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  if (params?.q) q.set('q', params.q);
  const qs = q.toString();
  return http<ProjectListResp>(`/projects${qs ? `?${qs}` : ''}`);
}

export async function createProjects(items: CreateProjectItem[]): Promise<Project[]> {
  return http<Project[]>('/projects', {
    method: 'POST',
    body: JSON.stringify({ items }),
  });
}

export async function updateProject(id: number, payload: ProjectUpdatePayload): Promise<Project> {
  return http<Project>(`/projects/${id}`, {
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
    try { const j = text ? JSON.parse(text) : null; msg = (j?.detail || msg); } catch {}
    const err = new Error(msg) as any;
    err.status = resp.status;
    throw err;
  }
  const data = await resp.json() as { access_token: string };
  localStorage.setItem('access_token', data.access_token);
}

export async function logout(): Promise<void> {
  try { localStorage.removeItem('access_token'); } catch {}
}


// -------- Лиды --------
export type Lead = {
  ext_id: number;
  project_id: number;
  created_at: string; // ISO string
  phone: string;
  utm_campaign?: string | null;
};

export type LeadsListResp = { items: Lead[]; total: number };

export async function fetchLeads(params: { projectIds?: number[]; fromDate: string; toDate: string; offset?: number; limit?: number; }): Promise<LeadsListResp> {
  const q = new URLSearchParams({
    fromDate: params.fromDate,
    toDate: params.toDate,
  });
  if (params.projectIds && params.projectIds.length > 0) {
    q.set('projectIds', params.projectIds.join(','));
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

export async function fetchReports(params?: { offset?: number; limit?: number }): Promise<ReportsListResp> {
  const q = new URLSearchParams();
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  const qs = q.toString();
  return http<ReportsListResp>(`/reports${qs ? `?${qs}` : ''}`);
}

export function buildLeadsExportUrl(params: { projectIds?: number[]; fromDate: string; toDate: string; format: 'csv'|'xlsx'; source?: 'leads' | 'reports'; }): string {
  const q = new URLSearchParams({ fromDate: params.fromDate, toDate: params.toDate, format: params.format });
  if (params.projectIds && params.projectIds.length > 0) q.set('projectIds', params.projectIds.join(','));
  if (params.source) q.set('source', params.source);
  // Добавляем токен авторизации в параметры запроса для экспорта
  const token = localStorage.getItem('access_token');
  if (token) q.set('token', token);
  return `${API_BASE}/leads/export?${q.toString()}`;
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
  status: 'Активен' | 'На паузе';
  deliveryStatus: 'Активна' | 'На модерации' | 'Отключена';
  dataLimit: number;
  regionMode: 'include' | 'exclude';
  regions: string[];
  sites?: string[];
  phones?: string[];
  smsSenderName?: string;
  days: Day[];
};

export async function fetchAdminUsers(): Promise<UserInfo[]> {
  return http<UserInfo[]>('/admin/users');
}

export async function fetchAdminProjects(params?: {
  offset?: number;
  limit?: number;
  q?: string;
  userId?: number;
}): Promise<AdminProjectListResp> {
  const q = new URLSearchParams();
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  if (params?.q) q.set('q', params.q);
  if (params?.userId != null) q.set('userId', String(params.userId));
  const qs = q.toString();
  return http<AdminProjectListResp>(`/admin/projects${qs ? `?${qs}` : ''}`);
}

export async function fetchAdminProject(id: number): Promise<AdminProject> {
  return http<AdminProject>(`/admin/projects/${id}`);
}

export async function updateAdminProject(id: number, payload: AdminProjectUpdate): Promise<AdminProject> {
  return http<AdminProject>(`/admin/projects/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function deleteAdminProject(id: number): Promise<void> {
  await http(`/admin/projects/${id}`, { method: 'DELETE' });
}

// -------- Админские лиды --------
export type AdminLead = Lead & {
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
  if (params.offset != null) q.set('offset', String(params.offset));
  if (params.limit != null) q.set('limit', String(params.limit));
  return http<AdminLeadsListResp>(`/admin/leads?${q.toString()}`);
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
};

export type AdminReportsListResp = {
  items: AdminReportItem[];
  total: number;
};

export async function fetchAdminReports(params?: {
  offset?: number;
  limit?: number;
  userId?: number;
}): Promise<AdminReportsListResp> {
  const q = new URLSearchParams();
  if (params?.offset != null) q.set('offset', String(params.offset));
  if (params?.limit != null) q.set('limit', String(params.limit));
  if (params?.userId != null) q.set('userId', String(params.userId));
  const qs = q.toString();
  return http<AdminReportsListResp>(`/admin/reports${qs ? `?${qs}` : ''}`);
}

// -------- Админские изменения клиентов --------
export type AdminChange = {
  id: number;
  projectId?: number | null;
  projectName?: string | null;
  createdAt: string;
  description: string;
};

export type AdminClientChangesOut = {
  user: UserInfo;
  items: AdminChange[];
};

export type AdminClientChangesSummaryItem = {
  user: UserInfo;
  pendingChanges: number;
};

export type AdminClientChangesSummaryListOut = {
  items: AdminClientChangesSummaryItem[];
};

export async function fetchAdminChangesSummary(): Promise<AdminClientChangesSummaryListOut> {
  return http<AdminClientChangesSummaryListOut>('/admin/changes/summary');
}

export async function fetchAdminClientChanges(clientId: number): Promise<AdminClientChangesOut> {
  return http<AdminClientChangesOut>(`/admin/changes/${clientId}`);
}

export async function resolveAdminChange(changeId: number): Promise<void> {
  await http(`/admin/changes/${changeId}/resolve`, { method: 'POST' });
}