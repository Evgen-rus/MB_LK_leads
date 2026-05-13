import { updateProject, type Day, type ProjectUpdatePayload } from '../api';
import type { Project } from '../types/project';
import { formatProjectNameForDisplay, formatSourceTextForDisplay } from './sourceCodeDisplay';

const DAYS_MAP: Record<string, Day> = {
  'Пн.': 'Пн',
  'Вт.': 'Вт',
  'Ср.': 'Ср',
  'Чт.': 'Чт',
  'Пт.': 'Пт',
  'Сб.': 'Сб',
  'Вс.': 'Вс',
};

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return formatSourceTextForDisplay(msg);
  }
  return formatSourceTextForDisplay(fallback);
}

function parseDaysFromRow(row: Project): Day[] {
  const parts = (row.daysReceived || '').split(/\s+/).filter(Boolean);
  const days: Day[] = [];
  parts.forEach((part) => {
    if (DAYS_MAP[part]) days.push(DAYS_MAP[part]);
  });
  return days.length > 0 ? days : (['Вт', 'Ср', 'Чт', 'Пт', 'Сб'] as Day[]);
}

export function buildUpdatePayloadFromProject(
  row: Project,
  patch: Partial<ProjectUpdatePayload>,
): ProjectUpdatePayload {
  const status = patch.status ?? (row.status === 'Блокировка оператора' ? 'Активен' : row.status);
  return {
    name: row.name,
    tag: row.tag || row.name,
    status,
    dataLimit: row.dataLimit,
    regionMode: row.regionMode || 'include',
    regions: row.regions || [],
    sites: row.sites || undefined,
    phones: row.phones || undefined,
    smsSenderName: row.smsSenderName || undefined,
    days: parseDaysFromRow(row),
    ...patch,
  };
}

export type BulkProgress = {
  total: number;
  done: number;
  updated: number;
  skipped: number;
  failed: number;
};

export type BulkRunResult = {
  updated: Project[];
  skipped: number;
  failed: number;
  warnings: string[];
  errors: string[];
  updatedItems: Array<{ id: number; name: string }>;
  skippedItems: Array<{ id: number; name: string }>;
  failedItems: Array<{ id: number; name: string; reason: string }>;
};

type RunBulkProjectUpdatesParams = {
  projects: Project[];
  buildPatch: (project: Project) => Partial<ProjectUpdatePayload> | null;
  onProgress?: (progress: BulkProgress) => void;
};

export async function runBulkProjectUpdatesSequential(
  params: RunBulkProjectUpdatesParams,
): Promise<BulkRunResult> {
  const { projects, buildPatch, onProgress } = params;
  const updatedProjects: Project[] = [];
  const warnings: string[] = [];
  const errors: string[] = [];
  const updatedItems: Array<{ id: number; name: string }> = [];
  const skippedItems: Array<{ id: number; name: string }> = [];
  const failedItems: Array<{ id: number; name: string; reason: string }> = [];

  let skipped = 0;
  let failed = 0;
  let done = 0;

  onProgress?.({
    total: projects.length,
    done,
    updated: updatedProjects.length,
    skipped,
    failed,
  });

  for (const project of projects) {
    const patch = buildPatch(project);

    if (!patch) {
      skipped += 1;
      skippedItems.push({ id: project.id, name: project.name });
      done += 1;
      onProgress?.({
        total: projects.length,
        done,
        updated: updatedProjects.length,
        skipped,
        failed,
      });
      continue;
    }

    try {
      const payload = buildUpdatePayloadFromProject(project, patch);
      const response = await updateProject(project.id, payload);
      updatedProjects.push(response.project);
      updatedItems.push({ id: response.project.id, name: formatProjectNameForDisplay(response.project.name) });
      if (response.warning) warnings.push(`Проект ${project.id} (${formatProjectNameForDisplay(project.name)}): ${formatSourceTextForDisplay(response.warning)}`);
    } catch (err: unknown) {
      failed += 1;
      const reason = getErrorMessage(err, 'Ошибка обновления');
      failedItems.push({ id: project.id, name: formatProjectNameForDisplay(project.name), reason });
      errors.push(`Проект ${project.id} (${formatProjectNameForDisplay(project.name)}): ${reason}`);
    } finally {
      done += 1;
      onProgress?.({
        total: projects.length,
        done,
        updated: updatedProjects.length,
        skipped,
        failed,
      });
    }
  }

  return {
    updated: updatedProjects,
    skipped,
    failed,
    warnings,
    errors,
    updatedItems,
    skippedItems,
    failedItems,
  };
}
