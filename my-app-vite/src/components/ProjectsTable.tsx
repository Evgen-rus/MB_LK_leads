// Таблица проектов: фильтры, список, метрики и столбец «Настройки»
import { useEffect, useMemo, useState, useCallback } from 'react';
import type { Day, ProjectUpdatePayload } from '../api';
import { fetchProjects, updateProject as apiUpdateProject, deleteProject as apiDeleteProject } from '../api';
import type { Project, ProjectStatus } from '../types/project';
import DateRangeFilter from './DateRangeFilter';
import BulkEditDaysModal from './BulkEditDaysModal';
import BulkEditLimitModal from './BulkEditLimitModal';
import BulkEditContactsModal, { type BulkEditContactsModalSubmit } from './BulkEditContactsModal';
import BulkEditRegionsModal from './BulkEditRegionsModal';
import BulkEditStatusModal from './BulkEditStatusModal';
import { buildUpdatePayloadFromProject, runBulkProjectUpdatesSequential, type BulkProgress } from '../utils/projectBulkUpdate';
import ProjectActionMenu from './ProjectActionMenu';
import DateTimeCompact from './DateTimeCompact';

type ProjectsTableProps = {
  onEdit?: (row: Project) => void;
  onCreate?: () => void;
  onHistory?: (row: Project) => void;
  onOpenLeads?: (params: { projectId: number; fromDate: string; toDate: string }) => void;
  projectsMutationLocked?: boolean;
  projectsMutationLockMessage?: string;
};

// Для режима "за всё время" нам всё равно нужен диапазон,
// потому что /leads требует fromDate/toDate. Даем максимально широкий интервал.
const ALL_TIME_FROM_DATE = '1970-01-01';
const ALL_TIME_TO_DATE = '2099-12-31';

function formatDateInput(d: Date) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

type BulkActionType = 'days' | 'limit' | 'contacts' | 'regions' | 'status';

const CALLS_SOURCES = new Set(['Звонки', 'Ретрозвонки', 'Пересечение']);
const SITES_SOURCES = new Set(['Сайты', 'Ретросайты', 'Пересечение']);
const SMS_SOURCE = 'СМС';
const SMS_EDIT_BLOCKED_MESSAGE =
  'Редактирование проектов с источником СМС временно недоступно. Обратитесь в техподдержку.';

type ApiError = Error & {
  status?: number;
  errorCode?: string | null;
};

function buildLimitControlBlockedMessage(rawReason?: string): string {
  const reason = (rawReason || '').trim();
  return [
    'Не удалось включить проект: сработал авто-контроль лимитов клиента.',
    reason || 'Сумма лимитов активных проектов превышает доступный остаток.',
    'Что сделать: уменьшите лимиты активных проектов или пополните баланс, затем повторите включение.',
  ].join('\n');
}

function ProjectsTable({
  onEdit,
  onCreate,
  onHistory,
  onOpenLeads,
  projectsMutationLocked = false,
  projectsMutationLockMessage = 'Изменение проектов временно заблокировано администратором.',
}: ProjectsTableProps) {
  const [rows, setRows] = useState<Project[]>([]);
  const [search, setSearch] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<'Все' | 'Активен' | 'На паузе' | 'Удалён'>('Все');
  const [fromDate, setFromDate] = useState<string>(formatDateInput(new Date()));
  const [toDate, setToDate] = useState<string>(formatDateInput(new Date()));
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [total, setTotal] = useState(0);
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [bulkMenuOpen, setBulkMenuOpen] = useState(false);
  const [openProjectMenuId, setOpenProjectMenuId] = useState<number | null>(null);
  const [projectMenuAnchorRect, setProjectMenuAnchorRect] = useState<DOMRect | null>(null);
  const [activeBulkAction, setActiveBulkAction] = useState<BulkActionType | null>(null);
  const [bulkSaving, setBulkSaving] = useState(false);
  const [bulkProgress, setBulkProgress] = useState<BulkProgress | null>(null);
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  // Не завязываем на state page/pageSize, чтобы клики пагинации не вызывали load(1)
  const load = useCallback(
    async (p: number, s = pageSize, q = search, from = fromDate, to = toDate, withDeleted = includeDeleted) => {
      const offset = (p - 1) * s;
      const resp = await fetchProjects({
        offset,
        limit: s,
        q: q.trim() || undefined,
        fromDate: from,
        toDate: to,
        includeDeleted: withDeleted,
      });
      setRows(resp.items);
      setTotal(resp.total);
    },
    [pageSize, search, fromDate, toDate, includeDeleted],
  );

  useEffect(() => { load(1); }, [load]);
  useEffect(() => {
    // Внешний сигнал обновить список
    const h = () => load(page, pageSize, search, fromDate, toDate, includeDeleted);
    window.addEventListener('projects-refresh', h);
    return () => window.removeEventListener('projects-refresh', h);
  }, [load, page, pageSize, search, fromDate, toDate, includeDeleted]);

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

  const filteredRows = useMemo<Project[]>(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((row) => {
      const nameHit = row.name.toLowerCase().includes(q);
      const idHit = String(row.id).includes(q);
      const matchesQuery = q === '' ? true : (nameHit || idHit);
      const matchesStatus = statusFilter === 'Все' ? true : row.status === statusFilter;
      return matchesQuery && matchesStatus;
    });
  }, [rows, search, statusFilter]);

  const selectableRows = useMemo(
    () => filteredRows.filter((row) => row.status !== 'Удалён'),
    [filteredRows],
  );
  const selectedRows = useMemo(
    () => filteredRows.filter((row) => selectedIds.includes(row.id)),
    [filteredRows, selectedIds],
  );
  const allSelectableOnPageSelected =
    selectableRows.length > 0 && selectableRows.every((row) => selectedIds.includes(row.id));

  useEffect(() => {
    // Выделение действует только в рамках текущей страницы/выборки.
    setSelectedIds([]);
    setBulkMenuOpen(false);
    setOpenProjectMenuId(null);
    setProjectMenuAnchorRect(null);
  }, [rows]);

  function toggleRowSelection(projectId: number) {
    setSelectedIds((prev) =>
      prev.includes(projectId) ? prev.filter((id) => id !== projectId) : [...prev, projectId],
    );
  }

  function toggleSelectAllOnPage() {
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

  function applyUpdatedProjects(updated: Project[]) {
    if (updated.length === 0) return;
    const map = new Map(updated.map((item) => [item.id, item]));
    setRows((prev) => prev.map((item) => map.get(item.id) ?? item));
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
    function formatNames(items: Array<{ id: number; name: string }>, max = 5): string {
      const preview = items
        .slice(0, max)
        .map((item) => `${item.name} (id: ${item.id})`)
        .join(', ');
      if (items.length <= max) return preview;
      return `${preview}, ... и еще ${items.length - max}`;
    }

    const lines: string[] = [];
    lines.push(`Обновлено: ${result.updatedCount}.`);
    if (result.updatedItems.length > 0) {
      lines.push(`Применено к: ${formatNames(result.updatedItems)}.`);
    }
    if (result.skippedCount > 0) {
      lines.push(
        result.skippedReasonLabel
          ? `Пропущено: ${result.skippedCount} (${result.skippedReasonLabel}).`
          : `Пропущено: ${result.skippedCount}.`,
      );
    }
    if (result.skippedItems.length > 0) {
      lines.push(`Не применено к: ${formatNames(result.skippedItems)}.`);
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
    window.dispatchEvent(new CustomEvent('app-toast', { detail: lines.join('\n') }));
  }

  async function runBulkAction(
    buildPatch: (project: Project) => Partial<ProjectUpdatePayload> | null,
    options?: { skippedReasonLabel?: string },
  ) {
    if (selectedRows.length === 0) return;
    if (projectsMutationLocked) {
      window.dispatchEvent(new CustomEvent('app-toast', { detail: projectsMutationLockMessage }));
      return;
    }
    setBulkSaving(true);
    setBulkProgress(null);
    try {
      const result = await runBulkProjectUpdatesSequential({
        projects: selectedRows,
        buildPatch,
        onProgress: setBulkProgress,
      });
      applyUpdatedProjects(result.updated);
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

  // Переключение статуса проекта (Активен <-> На паузе) для клиентского ЛК.
  // Это реальный PATCH на бэк; при ошибке статус визуально не меняется.
  async function handleToggleStatus(row: Project) {
    if (row.status === 'Удалён') return;
    if (projectsMutationLocked) {
      window.dispatchEvent(new CustomEvent('app-toast', { detail: projectsMutationLockMessage }));
      return;
    }
    if (row.collectionSource === SMS_SOURCE) {
      window.dispatchEvent(new CustomEvent('app-toast', { detail: SMS_EDIT_BLOCKED_MESSAGE }));
      return;
    }
    const nextStatus = row.status === 'Активен' ? 'На паузе' : 'Активен';
    try {
      const payload = buildUpdatePayloadFromProject(row, { status: nextStatus });
      const result = await apiUpdateProject(row.id, payload);
      setRows((prev) => prev.map((p) => (p.id === row.id ? result.project : p)));
      window.dispatchEvent(new CustomEvent('projects-refresh'));
      if (result.warning) {
        window.dispatchEvent(new CustomEvent('app-toast', { detail: result.warning }));
      }
    } catch (e) {
      console.error(e);
      const err = e as ApiError;
      if (err?.errorCode === 'LIMIT_CONTROL_BLOCK') {
        window.dispatchEvent(
          new CustomEvent('app-toast', {
            detail: buildLimitControlBlockedMessage(err.message),
          }),
        );
        return;
      }
      const message = e instanceof Error && e.message
        ? e.message
        : 'Не удалось изменить статус проекта.';
      window.dispatchEvent(new CustomEvent('app-toast', { detail: message }));
    }
  }

  async function handleSoftDelete(row: Project) {
    if (projectsMutationLocked) {
      window.dispatchEvent(new CustomEvent('app-toast', { detail: projectsMutationLockMessage }));
      return;
    }
    if (!window.confirm(`Удалить проект ${row.id} навсегда?`)) return;
    try {
      await apiDeleteProject(row.id);
      window.dispatchEvent(new CustomEvent('projects-refresh'));
    } catch (e) {
      console.error(e);
      const message = e instanceof Error && e.message
        ? e.message
        : 'Не удалось удалить проект. Попробуйте позже.';
      window.dispatchEvent(new CustomEvent('app-toast', { detail: message }));
    }
  }

  function handleBlockedSmsEditNotice() {
    window.dispatchEvent(new CustomEvent('app-toast', { detail: SMS_EDIT_BLOCKED_MESSAGE }));
  }

  async function handleBulkDaysSubmit(days: Day[]) {
    await runBulkAction(() => ({ days }));
  }

  async function handleBulkLimitSubmit(limit: number) {
    await runBulkAction(() => ({ dataLimit: limit }));
  }

  async function handleBulkRegionsSubmit(payload: { regions: string[]; regionMode: 'include' | 'exclude' }) {
    await runBulkAction((project) => {
      const mode = project.regionMode || 'include';
      if (mode !== payload.regionMode) return null;
      return { regions: payload.regions };
    }, {
      skippedReasonLabel:
        payload.regionMode === 'include'
          ? 'другой режим регионов: Исключить'
          : 'другой режим регионов: Включить',
    });
  }

  async function handleBulkStatusSubmit(status: Exclude<ProjectStatus, 'Удалён'>) {
    await runBulkAction(() => ({ status }));
  }

  async function handleBulkContactsSubmit(payload: BulkEditContactsModalSubmit) {
    await runBulkAction((project) => {
      if (payload.target === 'calls') {
        if (!CALLS_SOURCES.has(project.collectionSource)) return null;
        return { phones: payload.values };
      }
      if (!SITES_SOURCES.has(project.collectionSource)) return null;
      return { sites: payload.values };
    });
  }

  // Удаление из тулбара не используется — по просьбе отключено

  return (
    <div className="table-card">
      {projectsMutationLocked && (
        <div
          style={{
            margin: '12px',
            padding: '10px 12px',
            border: '1px solid #f2d59c',
            background: '#fff7e6',
            borderRadius: 10,
            color: '#8a5a00',
          }}
        >
          {projectsMutationLockMessage}
        </div>
      )}
      <div className="table-toolbar">
        <div className="filters" style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          {/* Период для пересчёта показателей проектов */}
          <DateRangeFilter
            from={fromDate}
            to={toDate}
            onChange={({ from, to }) => {
              setFromDate(from);
              setToDate(to);
              setPage(1);
              load(1, pageSize, search, from, to, includeDeleted);
            }}
          />

          <input
            type="search"
            placeholder="Поиск по названию/ID"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e)=> { if (e.key==='Enter') { setPage(1); load(1, pageSize, (e.target as HTMLInputElement).value, fromDate, toDate, includeDeleted); }}}
          />
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as 'Все' | 'Активен' | 'На паузе' | 'Удалён')}
          >
            <option value="Все">Все статусы проекта</option>
            <option value="Активен">Активен</option>
            <option value="На паузе">На паузе</option>
            <option value="Удалён">Удалён</option>
          </select>
          <label className="sub" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <input
              type="checkbox"
              checked={includeDeleted}
              onChange={(e) => {
                const val = e.target.checked;
                setIncludeDeleted(val);
                setPage(1);
                load(1, pageSize, search, fromDate, toDate, val);
              }}
            />
            Показывать удалённые
          </label>
        </div>
        <div className="actions">
          <button
            className="btn btn--primary"
            onClick={onCreate}
            disabled={projectsMutationLocked}
            title={projectsMutationLocked ? projectsMutationLockMessage : undefined}
          >
            + Добавить проект
          </button>
        </div>
      </div>
      {selectedRows.length > 0 && (
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
              disabled={bulkSaving || projectsMutationLocked}
              title={projectsMutationLocked ? projectsMutationLockMessage : undefined}
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
          <button className="pager__btn" disabled={page <= 1} onClick={() => { const p = Math.max(1, page - 1); setPage(p); load(p); }}>‹</button>
          <span className="pager__info">{page} / {totalPages}</span>
          <button className="pager__btn" disabled={page >= totalPages} onClick={() => { const p = Math.min(totalPages, page + 1); setPage(p); load(p); }}>›</button>
          <select className="pager__size" value={pageSize} onChange={(e) => { const s = Number(e.target.value); setPageSize(s); setPage(1); load(1, s); }}>
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
            <th style={{ width: 36 }}>
              <input
                type="checkbox"
                checked={allSelectableOnPageSelected}
                onChange={toggleSelectAllOnPage}
                title="Выбрать все доступные проекты на странице"
              />
            </th>
            <th>Название</th>
            <th>Источник</th>
            <th>Статус проекта</th>
            <th>Лимит</th>
            <th>Номеров за период</th>
            <th>Номеров получено всего</th>
            <th>Дни получения номеров</th>
            <th>Источник сбора</th>
            <th>Доменов/номеров</th>
            <th>Дата создания</th>
            <th>Действия</th>
          </tr>
        </thead>
        <tbody>
          {filteredRows.map((row) => (
            <tr key={row.id}>
              <td>
                <input
                  type="checkbox"
                  checked={selectedIds.includes(row.id)}
                  disabled={row.status === 'Удалён' || bulkSaving || projectsMutationLocked}
                  title={
                    projectsMutationLocked
                      ? projectsMutationLockMessage
                      : row.status === 'Удалён'
                      ? 'Удалённые проекты нельзя редактировать'
                      : 'Выбрать проект'
                  }
                  onChange={() => toggleRowSelection(row.id)}
                />
              </td>
              <td
                style={{ cursor: 'pointer', position: 'relative' }}
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
                title="Открыть меню действий проекта"
              >
                <div className="name">
                  {row.name}
                  <span className="project-id-badge">
                    id{row.id}
                  </span>
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
                          onOpenLeads({ projectId: row.id, fromDate, toDate });
                        },
                        disabled: !onOpenLeads,
                        title: !onOpenLeads ? 'Переход к идентификациям недоступен' : undefined,
                      },
                      {
                        key: 'settings',
                        label: 'Настройки проекта',
                        onSelect: () => {
                          if (projectsMutationLocked) {
                            window.dispatchEvent(new CustomEvent('app-toast', { detail: projectsMutationLockMessage }));
                            return;
                          }
                          if (row.collectionSource === SMS_SOURCE) {
                            handleBlockedSmsEditNotice();
                            return;
                          }
                          onEdit?.(row);
                        },
                        disabled: projectsMutationLocked || row.collectionSource === SMS_SOURCE,
                        title: projectsMutationLocked
                          ? projectsMutationLockMessage
                          : row.collectionSource === SMS_SOURCE
                            ? SMS_EDIT_BLOCKED_MESSAGE
                            : undefined,
                      },
                      {
                        key: 'history',
                        label: 'История изменений',
                        onSelect: () => onHistory?.(row),
                      },
                      {
                        key: 'delete',
                        label: 'Удаление проекта',
                        onSelect: () => {
                          if (row.status === 'Удалён') return;
                          handleSoftDelete(row);
                        },
                        disabled: projectsMutationLocked || row.status === 'Удалён',
                        danger: true,
                        title:
                          projectsMutationLocked
                            ? projectsMutationLockMessage
                            : row.status === 'Удалён'
                            ? 'Проект уже удален'
                            : 'Удалить проект навсегда',
                      },
                    ]}
                  />
                )}
              </td>
              <td>{row.dataSourceCode}</td>
              <td>
                <span
                  className={
                    row.status === 'Активен'
                      ? 'badge badge--green'
                      : row.status === 'На паузе'
                        ? 'badge badge--orange'
                        : 'badge badge--gray'
                  }
                  style={{
                    whiteSpace: 'nowrap',
                    cursor:
                      projectsMutationLocked || row.status === 'Удалён' || row.collectionSource === SMS_SOURCE
                        ? 'default'
                        : 'pointer',
                  }}
                  title={
                    projectsMutationLocked
                      ? projectsMutationLockMessage
                      : row.status === 'Удалён'
                      ? 'Проект помечен как удалённый'
                      : row.collectionSource === SMS_SOURCE
                        ? SMS_EDIT_BLOCKED_MESSAGE
                        : 'Нажмите, чтобы переключить статус проекта'
                  }
                  onClick={() => {
                    if (projectsMutationLocked) {
                      window.dispatchEvent(new CustomEvent('app-toast', { detail: projectsMutationLockMessage }));
                      return;
                    }
                    if (row.status === 'Удалён') return;
                    if (row.collectionSource === SMS_SOURCE) {
                      handleBlockedSmsEditNotice();
                      return;
                    }
                    handleToggleStatus(row);
                  }}
                >
                  {row.status}
                </span>
              </td>
              <td>{row.dataLimit}</td>
              <td
                style={{ cursor: onOpenLeads ? 'pointer' : 'default' }}
                onClick={() => {
                  if (!onOpenLeads) return;
                  onOpenLeads({ projectId: row.id, fromDate, toDate });
                }}
                title={onOpenLeads ? 'Открыть идентификации за выбранный период' : undefined}
              >
                {row.numbersPeriod ?? row.numbersToday}
              </td>
              <td
                style={{ cursor: onOpenLeads ? 'pointer' : 'default' }}
                onClick={() => {
                  if (!onOpenLeads) return;
                  // Для «Номеров всего» открываем лиды за всё время
                  onOpenLeads({
                    projectId: row.id,
                    fromDate: ALL_TIME_FROM_DATE,
                    toDate: ALL_TIME_TO_DATE,
                  });
                }}
                title={onOpenLeads ? 'Открыть все идентификации проекта (за всё время)' : undefined}
              >
                {row.numbersTotal}
              </td>
              <td className="muted">{row.daysReceived}</td>
              <td>{row.collectionSource}</td>
              <td>{row.sourcesCount}</td>
              <td className="muted"><DateTimeCompact value={row.createdAt} /></td>
              <td>
                <div style={{ display: 'flex', flexWrap: 'nowrap', gap: 2, alignItems: 'center' }}>
                  <button
                    className="icon-btn"
                    title="История изменений"
                    onClick={() => onHistory?.(row)}
                  >
                    📜
                  </button>
                  <button
                    className="icon-btn"
                    title={row.collectionSource === SMS_SOURCE ? SMS_EDIT_BLOCKED_MESSAGE : 'Настройки'}
                    onClick={() => {
                      if (row.collectionSource === SMS_SOURCE) {
                        handleBlockedSmsEditNotice();
                        return;
                      }
                      onEdit?.(row);
                    }}
                  >
                    ⚙️
                  </button>
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
                </div>
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
          <button className="pager__btn" disabled={page <= 1} onClick={() => { const p = Math.max(1, page - 1); setPage(p); load(p); }}>‹</button>
          <span className="pager__info">{page} / {totalPages}</span>
          <button className="pager__btn" disabled={page >= totalPages} onClick={() => { const p = Math.min(totalPages, page + 1); setPage(p); load(p); }}>›</button>
          <select className="pager__size" value={pageSize} onChange={(e) => { const s = Number(e.target.value); setPageSize(s); setPage(1); load(1, s); }}>
            <option value={10}>10</option>
            <option value={25}>25</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </div>
      </div>

      {activeBulkAction === 'days' && (
        <BulkEditDaysModal
          selectedCount={selectedRows.length}
          submitting={bulkSaving}
          onClose={closeBulkAction}
          onSubmit={handleBulkDaysSubmit}
        />
      )}
      {activeBulkAction === 'limit' && (
        <BulkEditLimitModal
          selectedCount={selectedRows.length}
          submitting={bulkSaving}
          onClose={closeBulkAction}
          onSubmit={handleBulkLimitSubmit}
        />
      )}
      {activeBulkAction === 'contacts' && (
        <BulkEditContactsModal
          selectedProjects={selectedRows}
          submitting={bulkSaving}
          onClose={closeBulkAction}
          onSubmit={handleBulkContactsSubmit}
        />
      )}
      {activeBulkAction === 'regions' && (
        <BulkEditRegionsModal
          selectedProjects={selectedRows}
          submitting={bulkSaving}
          onClose={closeBulkAction}
          onSubmit={handleBulkRegionsSubmit}
        />
      )}
      {activeBulkAction === 'status' && (
        <BulkEditStatusModal
          selectedCount={selectedRows.length}
          submitting={bulkSaving}
          onClose={closeBulkAction}
          onSubmit={handleBulkStatusSubmit}
        />
      )}
    </div>
  );
}

export default ProjectsTable;


