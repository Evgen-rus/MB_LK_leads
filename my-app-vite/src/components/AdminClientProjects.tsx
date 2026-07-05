// Экран «Проекты клиента» для админа.
// Показывает проекты только выбранного клиента в стиле обычной вкладки «Проекты».
import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import {
  deleteAdminProject,
  fetchAdminProjects,
  setAdminProjectTop,
  updateAdminProject,
  type AdminProject,
  type AdminProjectUpdate,
  type Day,
  type ProjectSortBy,
  type SortDir,
} from '../api';
import AdminEditProjectModal from './AdminEditProjectModal';
import AdminProjectHistoryModal from './AdminProjectHistoryModal';
import ProjectActionMenu from './ProjectActionMenu';
import DateTimeCompact from './DateTimeCompact';
import BulkEditDaysModal from './BulkEditDaysModal';
import BulkEditLimitModal from './BulkEditLimitModal';
import BulkEditContactsModal, { type BulkEditContactsModalSubmit } from './BulkEditContactsModal';
import BulkEditRegionsModal from './BulkEditRegionsModal';
import BulkEditStatusModal from './BulkEditStatusModal';
import BulkDeleteProjectsModal from './BulkDeleteProjectsModal';
import { runBulkProjectUpdatesSequential, type BulkProgress } from '../utils/projectBulkUpdate';
import type { ProjectMutableStatus } from '../types/project';
import { formatProjectNameForDisplay, formatProjectNameForSubmit, formatSourceTextForDisplay, toDisplaySourceCode } from '../utils/sourceCodeDisplay';
import ProjectChartModal from './ProjectChartModal';

type AdminClientProjectsProps = {
  clientId: number;
  clientName: string;
  fromDate: string;
  toDate: string;
  managerRole: 'admin' | 'agent';
  // Количество необработанных изменений по каждому проекту (projectId -> count)
  projectChanges?: Record<number, number>;
  // Количество необработанных созданий по каждому проекту (projectId -> count)
  projectCreates?: Record<number, number>;
  onOpenLeads?: (params: {
    clientId: number;
    clientName: string;
    projectId: number;
    fromDate: string;
    toDate: string;
  }) => void;
};
type ProjectStatusFilter = 'Все' | 'Активен' | 'На паузе' | 'Удалён' | 'Блокировка оператора';
type BulkActionType = 'days' | 'limit' | 'contacts' | 'regions' | 'status' | 'delete';

// Для режима "за всё время" нам всё равно нужен диапазон,
// потому что /admin/leads и /leads требуют fromDate/toDate. Даем максимально широкий интервал.
const ALL_TIME_FROM_DATE = '1970-01-01';
const ALL_TIME_TO_DATE = '2099-12-31';
const SEARCH_DEBOUNCE_MS = 400;
const OPERATOR_BLOCK_STATUS = 'Блокировка оператора';
const OPERATOR_BLOCK_TOOLTIP = 'В данном проекте мало номеров или мало трафика, поэтому его нужно расширить, чтобы проект снова смог работать. Рекомендуется добавить номера, объединить их в один пул и перезапустить проект.';

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return formatSourceTextForDisplay(msg);
  }
  return formatSourceTextForDisplay(fallback);
}

const ALL_DAYS: Day[] = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const DAYS_MAP: Record<string, Day> = {
  'Пн.': 'Пн',
  'Вт.': 'Вт',
  'Ср.': 'Ср',
  'Чт.': 'Чт',
  'Пт.': 'Пт',
  'Сб.': 'Сб',
  'Вс.': 'Вс',
};

function parseProjectDays(project: AdminProject): Day[] {
  const parts = (project.daysReceived || '').split(/\s+/).filter(Boolean);
  const days = parts
    .map((part) => DAYS_MAP[part])
    .filter((day): day is Day => Boolean(day));
  return days.length > 0 ? days : ALL_DAYS;
}

function AdminClientProjects({ clientId, clientName, fromDate, toDate, managerRole, onOpenLeads }: AdminClientProjectsProps) {
  const [rows, setRows] = useState<AdminProject[]>([]);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<ProjectStatusFilter>('Все');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(100);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<AdminProject | null>(null);
  const [editingReadOnly, setEditingReadOnly] = useState(false);
  const [historyFor, setHistoryFor] = useState<AdminProject | null>(null);
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [dailyLimitReached, setDailyLimitReached] = useState(false);
  const [topOnly, setTopOnly] = useState(false);
  const [openProjectMenuId, setOpenProjectMenuId] = useState<number | null>(null);
  const [projectMenuAnchorRect, setProjectMenuAnchorRect] = useState<DOMRect | null>(null);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [bulkMenuOpen, setBulkMenuOpen] = useState(false);
  const [activeBulkAction, setActiveBulkAction] = useState<BulkActionType | null>(null);
  const [bulkSaving, setBulkSaving] = useState(false);
  const [bulkProgress, setBulkProgress] = useState<BulkProgress | null>(null);
  const [chartFor, setChartFor] = useState<AdminProject | null>(null);
  const [topSavingIds, setTopSavingIds] = useState<Set<number>>(() => new Set());
  const [sortBy, setSortBy] = useState<ProjectSortBy>('id');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const canUseAdminProjectActions = managerRole === 'admin';

  async function load(
    p = page,
    s = pageSize,
    q = debouncedSearch,
    from = fromDate,
    to = toDate,
    withDeleted = includeDeleted,
    projectStatus = statusFilter,
    sortField = sortBy,
    direction = sortDir,
    limitReached = dailyLimitReached,
    topFilter = topOnly,
  ) {
    try {
      setLoading(true);
      setError(null);
      const offset = (p - 1) * s;
      const resp = await fetchAdminProjects({
        offset,
        limit: s,
        q: formatProjectNameForSubmit(q.trim()) || undefined,
        userId: clientId,
        fromDate: from,
        toDate: to,
        includeDeleted: withDeleted,
        projectStatus: projectStatus === 'Все' ? undefined : projectStatus,
        dailyLimitReached: limitReached,
        isTop: topFilter,
        sortBy: sortField,
        sortDir: direction,
      });
      setRows(resp.items);
      setTotal(resp.total);
    } catch (err: unknown) {
      console.error(err);
      setError(getErrorMessage(err, 'Не удалось загрузить проекты клиента'));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setPage(1);
      setDebouncedSearch(search);
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    load(1, pageSize, debouncedSearch, fromDate, toDate, includeDeleted, statusFilter, sortBy, sortDir, dailyLimitReached, topOnly);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId, debouncedSearch, fromDate, toDate, includeDeleted, statusFilter, sortBy, sortDir, dailyLimitReached, topOnly]);

  useEffect(() => {
    const h = () => load(page, pageSize, debouncedSearch, fromDate, toDate, includeDeleted, statusFilter, sortBy, sortDir, dailyLimitReached, topOnly);
    window.addEventListener('projects-refresh', h);
    return () => window.removeEventListener('projects-refresh', h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize, debouncedSearch, fromDate, toDate, includeDeleted, statusFilter, sortBy, sortDir, dailyLimitReached, topOnly]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const tableColSpan = canUseAdminProjectActions ? 14 : 13;
  const selectableRows = useMemo(
    () => rows.filter((row) => row.status !== 'Удалён' && row.status !== OPERATOR_BLOCK_STATUS),
    [rows],
  );
  const selectedRows = useMemo(
    () => rows.filter((row) => selectedIds.includes(row.id)),
    [rows, selectedIds],
  );
  const allSelectableOnPageSelected =
    selectableRows.length > 0 && selectableRows.every((row) => selectedIds.includes(row.id));

  const canEditProject = (project: AdminProject) => {
    void project;
    return true;
  };

  useEffect(() => {
    setOpenProjectMenuId(null);
    setProjectMenuAnchorRect(null);
    setSelectedIds([]);
    setBulkMenuOpen(false);
  }, [rows]);

  useEffect(() => {
    if (openProjectMenuId == null) return;
    const closeMenu = () => {
      setOpenProjectMenuId(null);
      setProjectMenuAnchorRect(null);
    };
    window.addEventListener('scroll', closeMenu, true);
    window.addEventListener('resize', closeMenu);
    return () => {
      window.removeEventListener('scroll', closeMenu, true);
      window.removeEventListener('resize', closeMenu);
    };
  }, [openProjectMenuId]);

  // Обновление проекта без открытия модалки (если потребуется)
  async function applyUpdate(project: AdminProject, patch: Partial<AdminProjectUpdate>) {
    try {
      const payload: AdminProjectUpdate = {
        name: project.name,
        tag: project.tag,
        status: patch.status ?? (project.status === OPERATOR_BLOCK_STATUS ? 'Активен' : project.status),
        deliveryStatus: project.deliveryStatus,
        dataLimit: project.dataLimit,
        regionMode: project.regionMode || 'include',
        regions: project.regions || [],
        sites: project.sites || undefined,
        phones: project.phones || undefined,
        smsSenderName: project.smsSenderName || undefined,
        days: parseProjectDays(project),
        ...patch,
      };
      const result = await updateAdminProject(project.id, payload);
      setRows((prev) => prev.map((p) => (p.id === result.project.id ? result.project : p)));
      if (result.warning) {
        window.dispatchEvent(new CustomEvent('app-toast', { detail: formatSourceTextForDisplay(result.warning) }));
      }
    } catch (err: unknown) {
      console.error(err);
      setError(getErrorMessage(err, 'Не удалось обновить проект'));
    }
  }

  // Переключение статуса проекта (Активен <-> На паузе) для админского экрана «Проекты клиента».
  async function handleToggleStatus(project: AdminProject) {
    if (project.status === 'Удалён') return;
    const nextStatus = project.status === 'Активен' ? 'На паузе' : 'Активен';
    await applyUpdate(project, { status: nextStatus });
  }

  async function handleToggleTop(project: AdminProject) {
    if (project.status === 'Удалён' || topSavingIds.has(project.id)) return;
    const nextIsTop = !project.isTop;
    setTopSavingIds((prev) => new Set(prev).add(project.id));
    setRows((prev) => prev.map((p) => (p.id === project.id ? { ...p, isTop: nextIsTop } : p)));
    try {
      const updated = await setAdminProjectTop(project.id, nextIsTop);
      setRows((prev) => prev.map((p) => (p.id === project.id ? updated : p)));
    } catch (err: unknown) {
      console.error(err);
      setRows((prev) => prev.map((p) => (p.id === project.id ? { ...p, isTop: project.isTop } : p)));
      window.dispatchEvent(new CustomEvent('app-toast', {
        detail: formatSourceTextForDisplay(getErrorMessage(err, 'Не удалось изменить отметку Топ')),
      }));
    } finally {
      setTopSavingIds((prev) => {
        const next = new Set(prev);
        next.delete(project.id);
        return next;
      });
    }
  }

  function handleSort(nextSortBy: ProjectSortBy) {
    const nextSortDir: SortDir = sortBy === nextSortBy && sortDir === 'asc' ? 'desc' : 'asc';
    setSortBy(nextSortBy);
    setSortDir(nextSortDir);
    setPage(1);
  }

  function renderSortableHeader(label: string, key: ProjectSortBy, style?: CSSProperties, thClassName?: string) {
    const active = sortBy === key;
    return (
      <th style={style} className={thClassName}>
        <button
          type="button"
          className={`table-sort${active ? ' table-sort--active' : ''}`}
          onClick={() => handleSort(key)}
          title={`Сортировать: ${label}`}
        >
          <span>{label}</span>
          <span className="table-sort__indicator">{active ? (sortDir === 'asc' ? '↑' : '↓') : '↕'}</span>
        </button>
      </th>
    );
  }

  function toggleRowSelection(projectId: number) {
    if (!canUseAdminProjectActions) return;
    setSelectedIds((prev) =>
      prev.includes(projectId) ? prev.filter((id) => id !== projectId) : [...prev, projectId],
    );
  }

  function toggleSelectAllOnPage() {
    if (!canUseAdminProjectActions) return;
    setSelectedIds((prev) => {
      const selectableIds = selectableRows.map((row) => row.id);
      if (allSelectableOnPageSelected) {
        return prev.filter((id) => !selectableIds.includes(id));
      }
      const union = new Set([...prev, ...selectableIds]);
      return Array.from(union);
    });
  }

  function openBulkAction(action: BulkActionType) {
    setBulkMenuOpen(false);
    setActiveBulkAction(action);
  }

  function closeBulkAction() {
    if (bulkSaving) return;
    setActiveBulkAction(null);
    setBulkProgress(null);
  }

  function buildAdminUpdatePayload(project: AdminProject, patch: Partial<AdminProjectUpdate>): AdminProjectUpdate {
    return {
      name: project.name,
      tag: project.tag,
      status: patch.status ?? (project.status === OPERATOR_BLOCK_STATUS ? 'Активен' : project.status),
      deliveryStatus: project.deliveryStatus,
      dataLimit: project.dataLimit,
      regionMode: project.regionMode || 'include',
      regions: project.regions || [],
      sites: project.sites || undefined,
      phones: project.phones || undefined,
      smsSenderName: project.smsSenderName || undefined,
      days: parseProjectDays(project),
      ...patch,
    };
  }

  function formatBulkItemNames(items: Array<{ id: number; name: string }>, max = 5): string {
    const preview = items
      .slice(0, max)
      .map((item) => `${formatProjectNameForDisplay(item.name)} (id: ${item.id})`)
      .join(', ');
    if (items.length <= max) return preview;
    return `${preview}, ... и еще ${items.length - max}`;
  }

  function showBulkResultToast(result: {
    updatedCount: number;
    skippedCount: number;
    failedCount: number;
    warnings: string[];
    errors: string[];
    updatedItems: Array<{ id: number; name: string }>;
    skippedItems: Array<{ id: number; name: string }>;
    failedItems: Array<{ id: number; name: string; reason: string }>;
    skippedReasonLabel?: string;
  }) {
    const lines: string[] = [`Обновлено: ${result.updatedCount}.`];
    if (result.updatedItems.length > 0) {
      lines.push(`Применено к: ${formatBulkItemNames(result.updatedItems)}.`);
    }
    if (result.skippedCount > 0) {
      lines.push(
        result.skippedReasonLabel
          ? `Пропущено: ${result.skippedCount} (${result.skippedReasonLabel}).`
          : `Пропущено: ${result.skippedCount}.`,
      );
    }
    if (result.skippedItems.length > 0) {
      lines.push(`Не применено к: ${formatBulkItemNames(result.skippedItems)}.`);
    }
    if (result.failedCount > 0) lines.push(`Ошибок: ${result.failedCount}.`);
    if (result.failedItems.length > 0) {
      const failedPreview = result.failedItems
        .slice(0, 3)
        .map((item) => `${item.name} (id: ${item.id}) - ${item.reason}`)
        .join('\n');
      lines.push(`Ошибки по проектам:\n${failedPreview}`);
      if (result.failedItems.length > 3) {
        lines.push(`... и еще ${result.failedItems.length - 3} проект(ов) с ошибкой.`);
      }
    }
    if (result.warnings.length > 0) lines.push(`Предупреждений: ${result.warnings.length}.`);
    if (result.errors.length > 0 && result.failedItems.length === 0) {
      const preview = result.errors.slice(0, 3).join('\n');
      lines.push(preview);
      if (result.errors.length > 3) {
        lines.push(`... и еще ${result.errors.length - 3}`);
      }
    }
    window.dispatchEvent(new CustomEvent('app-toast', { detail: formatSourceTextForDisplay(lines.join('\n')) }));
  }

  async function runBulkAction(
    buildPatch: (project: AdminProject) => Partial<AdminProjectUpdate> | null,
    options?: { skippedReasonLabel?: string },
  ) {
    if (!canUseAdminProjectActions || selectedRows.length === 0) return;
    setBulkSaving(true);
    setBulkProgress(null);
    try {
      const result = await runBulkProjectUpdatesSequential<AdminProject, AdminProjectUpdate>({
        projects: selectedRows,
        buildPatch,
        buildPayload: buildAdminUpdatePayload,
        updateProjectFn: updateAdminProject,
        onProgress: setBulkProgress,
      });
      setRows((prev) => {
        const updatedMap = new Map(result.updated.map((item) => [item.id, item]));
        return prev.map((item) => updatedMap.get(item.id) ?? item);
      });
      showBulkResultToast({
        updatedCount: result.updated.length,
        skippedCount: result.skipped,
        failedCount: result.failed,
        warnings: result.warnings,
        errors: result.errors,
        updatedItems: result.updatedItems,
        skippedItems: result.skippedItems,
        failedItems: result.failedItems,
        skippedReasonLabel: options?.skippedReasonLabel,
      });
      window.dispatchEvent(new CustomEvent('projects-refresh'));
      setSelectedIds([]);
      setActiveBulkAction(null);
      setBulkProgress(null);
    } finally {
      setBulkSaving(false);
    }
  }

  async function runBulkDeleteAction() {
    if (!canUseAdminProjectActions || selectedRows.length === 0) return;

    const deletedItems: Array<{ id: number; name: string }> = [];
    const failedItems: Array<{ id: number; name: string; reason: string }> = [];
    let done = 0;
    let failed = 0;

    setBulkSaving(true);
    setBulkProgress({
      total: selectedRows.length,
      done,
      updated: deletedItems.length,
      skipped: 0,
      failed,
    });

    try {
      for (const project of selectedRows) {
        try {
          await deleteAdminProject(project.id);
          deletedItems.push({ id: project.id, name: project.name });
        } catch (err: unknown) {
          failed += 1;
          const reason = err instanceof Error && err.message
            ? formatSourceTextForDisplay(err.message)
            : 'Ошибка удаления';
          failedItems.push({
            id: project.id,
            name: formatProjectNameForDisplay(project.name),
            reason,
          });
        } finally {
          done += 1;
          setBulkProgress({
            total: selectedRows.length,
            done,
            updated: deletedItems.length,
            skipped: 0,
            failed,
          });
        }
      }

      const lines: string[] = [`Удалено: ${deletedItems.length}.`];
      if (deletedItems.length > 0) {
        lines.push(`Удалены: ${formatBulkItemNames(deletedItems)}.`);
      }
      if (failedItems.length > 0) {
        lines.push(`Ошибок: ${failedItems.length}.`);
        const failedPreview = failedItems
          .slice(0, 3)
          .map((item) => `${item.name} (id: ${item.id}) - ${item.reason}`)
          .join('\n');
        lines.push(`Ошибки по проектам:\n${failedPreview}`);
        if (failedItems.length > 3) {
          lines.push(`... и еще ${failedItems.length - 3} проект(ов) с ошибкой.`);
        }
      }

      window.dispatchEvent(new CustomEvent('app-toast', { detail: formatSourceTextForDisplay(lines.join('\n')) }));
      window.dispatchEvent(new CustomEvent('projects-refresh'));
      setSelectedIds([]);
      setActiveBulkAction(null);
      setBulkProgress(null);
    } finally {
      setBulkSaving(false);
    }
  }

  async function handleSoftDelete(project: AdminProject) {
    if (!canUseAdminProjectActions || project.status === 'Удалён') return;
    if (!window.confirm(`Удалить проект ${formatProjectNameForDisplay(project.name)} (id: ${project.id}) навсегда?`)) return;
    try {
      await deleteAdminProject(project.id);
      window.dispatchEvent(new CustomEvent('projects-refresh'));
    } catch (err: unknown) {
      console.error(err);
      const message = err instanceof Error && err.message
        ? err.message
        : 'Не удалось удалить проект. Попробуйте позже.';
      window.dispatchEvent(new CustomEvent('app-toast', { detail: formatSourceTextForDisplay(message) }));
    }
  }

  async function handleBulkDaysSubmit(days: Day[]) {
    await runBulkAction(() => ({ days }));
  }

  async function handleBulkLimitSubmit(limit: number) {
    await runBulkAction(() => ({ dataLimit: limit }));
  }

  async function handleBulkRegionsSubmit(payload: { regions: string[]; regionMode: 'include' | 'exclude' }) {
    await runBulkAction((project) => {
      const currentMode = project.regionMode || 'include';
      if (currentMode !== payload.regionMode) return null;
      return { regions: payload.regions, regionMode: payload.regionMode };
    }, { skippedReasonLabel: 'другой режим регионов' });
  }

  async function handleBulkStatusSubmit(status: Exclude<ProjectMutableStatus, 'Удалён'>) {
    await runBulkAction(() => ({ status }));
  }

  async function handleBulkContactsSubmit(payload: BulkEditContactsModalSubmit) {
    await runBulkAction((project) => {
      const collectionSource = project.collectionSource;
      if (payload.target === 'calls') {
        if (!['Звонки', 'Ретрозвонки', 'Пересечение'].includes(collectionSource)) return null;
        return { phones: payload.values };
      }
      if (!['Сайты', 'Ретросайты', 'Пересечение'].includes(collectionSource)) return null;
      return { sites: payload.values };
    }, { skippedReasonLabel: 'другой источник сбора' });
  }

  return (
    <div className="table-card">
      <div className="table-toolbar">
        <div className="filters">
          <input
            type="search"
            placeholder="Поиск по названию проекта"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
            }}
          />
          <select
            value={statusFilter}
            onChange={(e) => {
              const nextStatus = e.target.value as ProjectStatusFilter;
              setStatusFilter(nextStatus);
              setPage(1);
            }}
          >
            <option value="Все">Все статусы проекта</option>
            <option value="Активен">Активен</option>
            <option value="На паузе">На паузе</option>
            <option value="Удалён">Удалён</option>
            <option value="Блокировка оператора">Блокировка оператора</option>
          </select>
          <div className="project-quick-filters">
            <button
              type="button"
              className={`btn project-filter-toggle ${dailyLimitReached ? 'btn--primary' : 'btn--secondary'}`}
              aria-pressed={dailyLimitReached}
              title="Показать проекты, где за выбранный период получено данных не меньше дневного лимита"
              onClick={() => {
                const nextValue = !dailyLimitReached;
                setDailyLimitReached(nextValue);
                setPage(1);
              }}
            >
              100%
            </button>
            <button
              type="button"
              className={`btn project-filter-toggle project-filter-toggle--icon ${topOnly ? 'btn--primary' : 'btn--secondary'}`}
              aria-pressed={topOnly}
              title={topOnly ? 'Показать все проекты' : 'Только топ-проекты'}
              onClick={() => {
                const nextValue = !topOnly;
                setTopOnly(nextValue);
                setPage(1);
              }}
            >
              ★
            </button>
          </div>
          <label className="sub" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <input
              type="checkbox"
              checked={includeDeleted}
              onChange={(e) => {
                const val = e.target.checked;
                setIncludeDeleted(val);
                setPage(1);
              }}
            />
            Показывать удалённые
          </label>
        </div>
        <div className="actions">
          <span className="sub">
            Проекты клиента: {clientName} (id: {clientId}) — всего {total}
          </span>
        </div>
      </div>
      {canUseAdminProjectActions && selectedRows.length > 0 && (
        <div
          style={{
            margin: '0 12px 12px',
            padding: '10px 12px',
            border: '1px solid #ece7ff',
            background: '#f7f4ff',
            borderRadius: 10,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            flexWrap: 'wrap',
            position: 'relative',
          }}
        >
          <span className="badge badge--gray">Выбрано: {selectedRows.length}</span>
          <button className="btn btn--ghost" onClick={() => setSelectedIds([])} disabled={bulkSaving}>
            Снять выделение
          </button>
          <div style={{ position: 'relative' }}>
            <button
              className="btn btn--primary"
              onClick={() => setBulkMenuOpen((prev) => !prev)}
              disabled={bulkSaving}
            >
              Массовые действия
            </button>
            {bulkMenuOpen && (
              <div
                style={{
                  position: 'absolute',
                  top: 'calc(100% + 6px)',
                  left: 0,
                  minWidth: 230,
                  zIndex: 20,
                  border: '1px solid #e7e7ef',
                  borderRadius: 10,
                  background: '#fff',
                  boxShadow: '0 10px 25px rgba(0, 0, 0, 0.12)',
                  padding: 6,
                  display: 'grid',
                  gap: 2,
                }}
              >
                <button className="btn btn--ghost" onClick={() => openBulkAction('days')}>Дни получения данных</button>
                <button className="btn btn--ghost" onClick={() => openBulkAction('limit')}>Лимит</button>
                <button className="btn btn--ghost" onClick={() => openBulkAction('contacts')}>Телефоны/сайты конкурентов</button>
                <button className="btn btn--ghost" onClick={() => openBulkAction('regions')}>Регионы</button>
                <button className="btn btn--ghost" onClick={() => openBulkAction('status')}>Статус проекта</button>
                <button className="btn btn--ghost" onClick={() => openBulkAction('delete')}>Удалить проекты</button>
              </div>
            )}
          </div>
          {bulkSaving && bulkProgress && (
            <span className="sub" style={{ color: '#6b4ce6' }}>
              Обработка: {bulkProgress.done}/{bulkProgress.total}
            </span>
          )}
        </div>
      )}
      <div className="table-footer table-footer--top">
        Показано {rows.length} из {total}
        <div className="spacer" />
        <div className="pager">
          <button
            className="pager__btn"
            disabled={page <= 1}
            onClick={() => {
              const p = Math.max(1, page - 1);
              setPage(p);
              load(p);
            }}
          >
            ‹
          </button>
          <span className="pager__info">
            {page} / {totalPages}
          </span>
          <button
            className="pager__btn"
            disabled={page >= totalPages}
            onClick={() => {
              const p = Math.min(totalPages, page + 1);
              setPage(p);
              load(p);
            }}
          >
            ›
          </button>
          <select
            className="pager__size"
            value={pageSize}
            onChange={(e) => {
              const s = Number(e.target.value);
              setPageSize(s);
              setPage(1);
              load(1, s);
            }}
          >
            <option value={10}>10</option>
            <option value={25}>25</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </div>
      </div>

      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              {canUseAdminProjectActions && (
                <th className="table-sticky-cell table-sticky-cell--check">
                  <input
                    type="checkbox"
                    checked={allSelectableOnPageSelected}
                    onChange={toggleSelectAllOnPage}
                    title="Выбрать все доступные проекты на странице"
                  />
                </th>
              )}
              {renderSortableHeader(
                'ID',
                'id',
                { width: 20 },
                canUseAdminProjectActions
                  ? 'table-sticky-cell table-sticky-cell--after-check'
                  : 'table-sticky-cell table-sticky-cell--lead',
              )}
              <th className="project-top-cell">Топ</th>
              {renderSortableHeader('Название', 'name')}
              {renderSortableHeader('Источник', 'dataSourceCode')}
              {renderSortableHeader('Статус проекта', 'status')}
              {renderSortableHeader('Лимит', 'dataLimit')}
              {renderSortableHeader('Номеров за период', 'numbersPeriod')}
              {renderSortableHeader('Номеров получено всего', 'numbersTotal')}
              <th>Дни получения номеров</th>
              {renderSortableHeader('Источник сбора', 'collectionSource')}
              {renderSortableHeader('Доменов/номеров', 'sourcesCount')}
              {renderSortableHeader('Дата создания', 'createdAt')}
              <th>Действия</th>
            </tr>
          </thead>
          <tbody>
            {error && (
              <tr>
                <td colSpan={tableColSpan} style={{ color: '#d00', padding: 16 }}>
                  {error}
                </td>
              </tr>
            )}
            {!error && loading && (
              <tr>
                <td colSpan={tableColSpan} className="muted" style={{ padding: 16 }}>
                  Загрузка проектов…
                </td>
              </tr>
            )}
            {!error && !loading && rows.length === 0 && (
              <tr>
                <td colSpan={tableColSpan} className="muted" style={{ padding: 16 }}>
                  Проекты клиента не найдены.
                </td>
              </tr>
            )}
            {!error &&
              !loading &&
              rows.map((row) => (
                <tr key={row.id}>
                  {canUseAdminProjectActions && (
                    <td className="table-sticky-cell table-sticky-cell--check">
                      <input
                        type="checkbox"
                        checked={selectedIds.includes(row.id)}
                        disabled={row.status === 'Удалён' || row.status === OPERATOR_BLOCK_STATUS || bulkSaving}
                        title={
                          row.status === 'Удалён'
                            ? 'Удалённые проекты нельзя редактировать'
                            : row.status === OPERATOR_BLOCK_STATUS
                              ? 'Проекты с блокировкой оператора не участвуют в массовых действиях'
                              : 'Выбрать проект'
                        }
                        onChange={() => toggleRowSelection(row.id)}
                      />
                    </td>
                  )}
                  <td className={`muted table-sticky-cell ${canUseAdminProjectActions ? 'table-sticky-cell--after-check' : 'table-sticky-cell--lead'}`}>{row.id}</td>
                  <td className="project-top-cell">
                    <button
                      type="button"
                      className={`project-top-button${row.isTop ? ' project-top-button--active' : ''}`}
                      aria-pressed={row.isTop}
                      disabled={row.status === 'Удалён' || topSavingIds.has(row.id)}
                      title={
                        row.status === 'Удалён'
                          ? row.isTop ? 'Топ' : 'Удалённый проект нельзя отмечать как топ'
                          : row.isTop ? 'Топ' : 'Отметить как топ'
                      }
                      onClick={() => handleToggleTop(row)}
                    >
                      {row.isTop ? '★' : '☆'}
                    </button>
                  </td>
                  <td
                    style={{ cursor: 'pointer', position: 'relative' }}
                    title="Открыть меню действий проекта"
                    onClick={(event) => {
                      const nextRect = event.currentTarget.getBoundingClientRect();
                      setOpenProjectMenuId((prev) => {
                        if (prev === row.id) {
                          setProjectMenuAnchorRect(null);
                          return null;
                        }
                        setProjectMenuAnchorRect(nextRect);
                        return row.id;
                      });
                    }}
                  >
                    <div className="name">
                      {formatProjectNameForDisplay(row.name)}
                    </div>
                  {openProjectMenuId === row.id && projectMenuAnchorRect && (
                    <ProjectActionMenu
                      onClose={() => {
                        setOpenProjectMenuId(null);
                        setProjectMenuAnchorRect(null);
                      }}
                      anchorRect={projectMenuAnchorRect}
                      items={[
                        {
                          key: 'identifications',
                          label: 'Идентификации проекта',
                          onSelect: () => {
                            if (!onOpenLeads) return;
                            onOpenLeads({
                              clientId,
                              clientName,
                              projectId: row.id,
                              fromDate,
                              toDate,
                            });
                          },
                          disabled: !onOpenLeads,
                          title: !onOpenLeads ? 'Переход к идентификациям недоступен' : undefined,
                        },
                        ...(canUseAdminProjectActions
                          ? [
                              {
                                key: 'chart',
                                label: 'График данных',
                                onSelect: () => setChartFor(row),
                              },
                            ]
                          : []),
                        {
                          key: 'settings',
                          label: 'Настройки проекта',
                          onSelect: () => {
                            setEditing(row);
                            setEditingReadOnly(!canEditProject(row));
                          },
                        },
                        {
                          key: 'history',
                          label: 'История изменений',
                          onSelect: () => setHistoryFor(row),
                        },
                        ...(canUseAdminProjectActions
                          ? [
                              {
                                key: 'delete',
                                label: 'Удаление проекта',
                                onSelect: () => {
                                  if (row.status === 'Удалён') return;
                                  handleSoftDelete(row);
                                },
                                disabled: row.status === 'Удалён',
                                danger: true,
                                title: row.status === 'Удалён'
                                  ? 'Проект уже удален'
                                  : 'Удалить проект навсегда',
                              },
                            ]
                          : []),
                      ]}
                    />
                  )}
                  </td>
                  <td>{toDisplaySourceCode(row.dataSourceCode)}</td>
                  <td>
                    <span className="project-status-inline">
                      <span
                        className={
                          row.status === 'Активен'
                            ? 'badge badge--green'
                            : row.status === 'На паузе'
                              ? 'badge badge--orange'
                              : row.status === OPERATOR_BLOCK_STATUS
                                ? 'badge badge--red'
                                : 'badge badge--gray'
                        }
                        style={{ whiteSpace: 'nowrap', cursor: row.status === 'Удалён' ? 'default' : 'pointer' }}
                        title={
                          row.status === 'Удалён'
                            ? 'Проект помечен как удалённый'
                            : row.status === OPERATOR_BLOCK_STATUS
                              ? 'Нажмите, чтобы перезапустить проект'
                              : 'Нажмите, чтобы переключить статус проекта'
                        }
                        onClick={() => {
                          if (row.status === 'Удалён') return;
                          handleToggleStatus(row);
                        }}
                      >
                        {row.status}
                      </span>
                      {row.status === OPERATOR_BLOCK_STATUS && (
                        <span
                          className="operator-block-info"
                          title={OPERATOR_BLOCK_TOOLTIP}
                          aria-label="Пояснение к блокировке оператора"
                        >
                          i
                        </span>
                      )}
                    </span>
                  </td>
                  <td>{row.dataLimit}</td>
                  <td
                    style={{ cursor: onOpenLeads ? 'pointer' : 'default' }}
                    title={onOpenLeads ? 'Идентификации за текущий период' : undefined}
                    onClick={() => {
                      if (!onOpenLeads) return;
                      onOpenLeads({
                        clientId,
                        clientName,
                        projectId: row.id,
                        fromDate,
                        toDate,
                      });
                    }}
                  >
                    {row.numbersPeriod ?? row.numbersToday}
                  </td>
                  <td
                    style={{ cursor: onOpenLeads ? 'pointer' : 'default' }}
                    title={onOpenLeads ? 'Идентификации за весь срок проекта' : undefined}
                    onClick={() => {
                      if (!onOpenLeads) return;
                      onOpenLeads({
                        clientId,
                        clientName,
                        projectId: row.id,
                        fromDate: ALL_TIME_FROM_DATE,
                        toDate: ALL_TIME_TO_DATE,
                      });
                    }}
                  >
                    {row.numbersTotal}
                  </td>
                  <td className="muted">{row.daysReceived}</td>
                  <td>{row.collectionSource}</td>
                  <td>{row.sourcesCount}</td>
                  <td className="muted"><DateTimeCompact value={row.createdAt} /></td>
                  <td>
                    {(() => {
                      const canEdit = canEditProject(row);
                      return (
                        <div style={{ display: 'flex', flexWrap: 'nowrap', gap: 2, alignItems: 'center' }}>
                          {canUseAdminProjectActions && (
                            <button
                              className="icon-btn"
                              title="Показать график данных"
                              onClick={() => setChartFor(row)}
                            >
                              📈
                            </button>
                          )}
                          <button
                            className="icon-btn"
                            title={
                              canEdit
                                ? 'Редактировать проект'
                                : 'Только просмотр (редактировать свои или через ЛК клиента)'
                            }
                            onClick={() => {
                              setEditing(row);
                              setEditingReadOnly(!canEdit);
                            }}
                          >
                            ⚙️
                          </button>
                          <button
                            className="icon-btn"
                            title="История изменений"
                            onClick={() => setHistoryFor(row)}
                          >
                            📜
                          </button>
                          {canUseAdminProjectActions && (
                            <button
                              className="icon-btn"
                              title={row.status === 'Удалён' ? 'Проект уже удален' : 'Удалить проект навсегда'}
                              onClick={() => {
                                if (row.status === 'Удалён') return;
                                handleSoftDelete(row);
                              }}
                            >
                              🗑️
                            </button>
                          )}
                        </div>
                      );
                    })()}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      <div className="table-footer">
        Показано {rows.length} из {total}
        <div className="spacer" />
        <div className="pager">
          <button
            className="pager__btn"
            disabled={page <= 1}
            onClick={() => {
              const p = Math.max(1, page - 1);
              setPage(p);
              load(p);
            }}
          >
            ‹
          </button>
          <span className="pager__info">
            {page} / {totalPages}
          </span>
          <button
            className="pager__btn"
            disabled={page >= totalPages}
            onClick={() => {
              const p = Math.min(totalPages, page + 1);
              setPage(p);
              load(p);
            }}
          >
            ›
          </button>
          <select
            className="pager__size"
            value={pageSize}
            onChange={(e) => {
              const s = Number(e.target.value);
              setPageSize(s);
              setPage(1);
              load(1, s);
            }}
          >
            <option value={10}>10</option>
            <option value={25}>25</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </div>
      </div>

      {editing && (
        <AdminEditProjectModal
          project={editing}
          readOnly={editingReadOnly}
          allowDelete={canUseAdminProjectActions}
          onClose={() => {
            setEditing(null);
            setEditingReadOnly(false);
          }}
          onSubmit={(updated) => {
            setRows((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
            setEditing(null);
            setEditingReadOnly(false);
          }}
        />
      )}
      {historyFor && (
        <AdminProjectHistoryModal
          projectId={historyFor.id}
          projectName={historyFor.name}
          onClose={() => setHistoryFor(null)}
        />
      )}
      {canUseAdminProjectActions && activeBulkAction === 'days' && (
        <BulkEditDaysModal
          selectedCount={selectedRows.length}
          submitting={bulkSaving}
          progress={bulkProgress}
          onClose={closeBulkAction}
          onSubmit={handleBulkDaysSubmit}
        />
      )}
      {canUseAdminProjectActions && activeBulkAction === 'limit' && (
        <BulkEditLimitModal
          selectedCount={selectedRows.length}
          submitting={bulkSaving}
          progress={bulkProgress}
          onClose={closeBulkAction}
          onSubmit={handleBulkLimitSubmit}
        />
      )}
      {canUseAdminProjectActions && activeBulkAction === 'contacts' && (
        <BulkEditContactsModal
          selectedProjects={selectedRows}
          submitting={bulkSaving}
          progress={bulkProgress}
          onClose={closeBulkAction}
          onSubmit={handleBulkContactsSubmit}
        />
      )}
      {canUseAdminProjectActions && activeBulkAction === 'regions' && (
        <BulkEditRegionsModal
          selectedProjects={selectedRows}
          submitting={bulkSaving}
          progress={bulkProgress}
          onClose={closeBulkAction}
          onSubmit={handleBulkRegionsSubmit}
        />
      )}
      {canUseAdminProjectActions && activeBulkAction === 'status' && (
        <BulkEditStatusModal
          selectedCount={selectedRows.length}
          submitting={bulkSaving}
          progress={bulkProgress}
          onClose={closeBulkAction}
          onSubmit={handleBulkStatusSubmit}
        />
      )}
      {canUseAdminProjectActions && activeBulkAction === 'delete' && (
        <BulkDeleteProjectsModal
          selectedProjects={selectedRows}
          submitting={bulkSaving}
          progress={bulkProgress}
          onClose={closeBulkAction}
          onSubmit={runBulkDeleteAction}
        />
      )}
      {canUseAdminProjectActions && chartFor && (
        <ProjectChartModal
          projectId={chartFor.id}
          projectName={chartFor.name}
          fromDate={fromDate}
          toDate={toDate}
          mode="admin"
          onClose={() => setChartFor(null)}
        />
      )}
    </div>
  );
}

export default AdminClientProjects;


