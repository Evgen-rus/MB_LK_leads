// Экран «Проекты клиента» для админа.
// Показывает проекты только выбранного клиента в стиле обычной вкладки «Проекты».
import { useEffect, useState, type CSSProperties } from 'react';
import {
  fetchAdminProjects,
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
import { formatProjectNameForDisplay, formatProjectNameForSubmit, formatSourceTextForDisplay, toDisplaySourceCode } from '../utils/sourceCodeDisplay';

type AdminClientProjectsProps = {
  clientId: number;
  clientName: string;
  fromDate: string;
  toDate: string;
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

function AdminClientProjects({ clientId, clientName, fromDate, toDate, onOpenLeads }: AdminClientProjectsProps) {
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
  const [openProjectMenuId, setOpenProjectMenuId] = useState<number | null>(null);
  const [projectMenuAnchorRect, setProjectMenuAnchorRect] = useState<DOMRect | null>(null);
  const [sortBy, setSortBy] = useState<ProjectSortBy>('id');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

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
    load(1, pageSize, debouncedSearch, fromDate, toDate, includeDeleted, statusFilter, sortBy, sortDir, dailyLimitReached);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId, debouncedSearch, fromDate, toDate, includeDeleted, statusFilter, sortBy, sortDir, dailyLimitReached]);

  useEffect(() => {
    const h = () => load(page, pageSize, debouncedSearch, fromDate, toDate, includeDeleted, statusFilter, sortBy, sortDir, dailyLimitReached);
    window.addEventListener('projects-refresh', h);
    return () => window.removeEventListener('projects-refresh', h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, pageSize, debouncedSearch, fromDate, toDate, includeDeleted, statusFilter, sortBy, sortDir, dailyLimitReached]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const canEditProject = (project: AdminProject) => {
    void project;
    return true;
  };

  useEffect(() => {
    setOpenProjectMenuId(null);
    setProjectMenuAnchorRect(null);
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
              load(1, pageSize, search, fromDate, toDate, includeDeleted, nextStatus);
            }}
          >
            <option value="Все">Все статусы проекта</option>
            <option value="Активен">Активен</option>
            <option value="На паузе">На паузе</option>
            <option value="Удалён">Удалён</option>
            <option value="Блокировка оператора">Блокировка оператора</option>
          </select>
          <button
            type="button"
            className={dailyLimitReached ? 'btn btn--primary' : 'btn btn--secondary'}
            aria-pressed={dailyLimitReached}
            title="Показать проекты, где за выбранный период получено данных не меньше дневного лимита"
            onClick={() => {
              const nextValue = !dailyLimitReached;
              setDailyLimitReached(nextValue);
              setPage(1);
              load(1, pageSize, search, fromDate, toDate, includeDeleted, statusFilter, sortBy, sortDir, nextValue);
            }}
          >
            100%
          </button>
          <label className="sub" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <input
              type="checkbox"
              checked={includeDeleted}
              onChange={(e) => {
                const val = e.target.checked;
                setIncludeDeleted(val);
                setPage(1);
                load(1, pageSize, search, fromDate, toDate, val, statusFilter);
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
              {renderSortableHeader('ID', 'id', { width: 20 }, 'table-sticky-cell table-sticky-cell--lead')}
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
                <td colSpan={12} style={{ color: '#d00', padding: 16 }}>
                  {error}
                </td>
              </tr>
            )}
            {!error && loading && (
              <tr>
                <td colSpan={12} className="muted" style={{ padding: 16 }}>
                  Загрузка проектов…
                </td>
              </tr>
            )}
            {!error && !loading && rows.length === 0 && (
              <tr>
                <td colSpan={12} className="muted" style={{ padding: 16 }}>
                  Проекты клиента не найдены.
                </td>
              </tr>
            )}
            {!error &&
              !loading &&
              rows.map((row) => (
                <tr key={row.id}>
                  <td className="muted table-sticky-cell table-sticky-cell--lead">{row.id}</td>
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
    </div>
  );
}

export default AdminClientProjects;


