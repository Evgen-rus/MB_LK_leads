// Файл: src/api.ts
// Назначение: HTTP-клиент фронтенда для работы с бэкендом (projects, client-errors).
import type { Project } from './types/project';

const API_BASE = (import.meta as any).env?.VITE_API_BASE || 'http://localhost:8000';

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
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
    },
    credentials: 'include',
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    const err = new Error(text || res.statusText) as any;
    (err.status = res.status);
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
  await http('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
}

export async function logout(): Promise<void> {
  await http('/auth/logout', { method: 'POST' });
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

export function buildLeadsExportUrl(params: { projectIds?: number[]; fromDate: string; toDate: string; format: 'csv'|'xlsx'; }): string {
  const q = new URLSearchParams({ fromDate: params.fromDate, toDate: params.toDate, format: params.format });
  if (params.projectIds && params.projectIds.length > 0) q.set('projectIds', params.projectIds.join(','));
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

