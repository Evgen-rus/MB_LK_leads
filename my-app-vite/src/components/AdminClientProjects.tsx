// Экран «Проекты клиента» для админа.
// Показывает проекты только выбранного клиента в стиле обычной вкладки «Проекты».
import { useEffect, useState } from 'react';
import { fetchAdminProjects, updateAdminProject, type AdminProject, type AdminProjectUpdate, type Day } from '../api';
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

// Для режима "за всё время" нам всё равно нужен диапазон,
// потому что /admin/leads и /leads требуют fromDate/toDate. Даем максимально широкий интервал.
const ALL_TIME_FROM_DATE = '1970-01-01';
const ALL_TIME_TO_DATE = '2099-12-31';
const SMS_SOURCE = 'СМС';
const SMS_EDIT_BLOCKED_MESSAGE =
  'Редактирование проектов с источником СМС временно недоступно. Обратитесь в техподдержку.';

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return formatSourceTextForDisplay(msg);
  }
  return formatSourceTextForDisplay(fallback);
}

const ALL_DAYS: Day[] = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

function AdminClientProjects({ clientId, clientName, fromDate, toDate, onOpenLeads }: AdminClientProjectsProps) {
  const [rows, setRows] = useState<AdminProject[]>([]);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<'Все' | 'Активен' | 'На паузе' | 'Удалён'>('Все');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(100);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<AdminProject | null>(null);
  const [editingReadOnly, setEditingReadOnly] = useState(false);
  const [historyFor, setHistoryFor] = useState<AdminProject | null>(null);
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [openProjectMenuId, setOpenProjectMenuId] = useState<number | null>(null);
  const [projectMenuAnchorRect, setProjectMenuAnchorRect] = useState<DOMRect | null>(null);

  async function load(
    p = page,
    s = pageSize,
    q = search,
    from = fromDate,
    to = toDate,
    withDeleted = includeDeleted,
    projectStatus = statusFilter,
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
    load(1, pageSize, search, fromDate, toDate, includeDeleted, statusFilter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId, fromDate, toDate, includeDeleted, statusFilter]);

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
        status: project.status,
        deliveryStatus: project.deliveryStatus,
        dataLimit: project.dataLimit,
        regionMode: project.regionMode || 'include',
        regions: project.regions || [],
        sites: project.sites || undefined,
        phones: project.phones || undefined,
        smsSenderName: project.smsSenderName || undefined,
        days: ALL_DAYS,
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
    if (project.collectionSource === SMS_SOURCE) {
      window.dispatchEvent(new CustomEvent('app-toast', { detail: SMS_EDIT_BLOCKED_MESSAGE }));
      return;
    }
    const nextStatus = project.status === 'Активен' ? 'На паузе' : 'Активен';
    await applyUpdate(project, { status: nextStatus });
  }

  function handleBlockedSmsEditNotice() {
    window.dispatchEvent(new CustomEvent('app-toast', { detail: SMS_EDIT_BLOCKED_MESSAGE }));
  }

  return (
    <div className="table-card">
      <div className="table-toolbar">
        <div className="filters">
          <input
            type="search"
            placeholder="Поиск по названию/ID проекта клиента"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                setPage(1);
                load(1, pageSize, (e.target as HTMLInputElement).value, fromDate, toDate, includeDeleted, statusFilter);
              }
            }}
          />
          <select
            value={statusFilter}
            onChange={(e) => {
              const nextStatus = e.target.value as 'Все' | 'Активен' | 'На паузе' | 'Удалён';
              setStatusFilter(nextStatus);
              setPage(1);
              load(1, pageSize, search, fromDate, toDate, includeDeleted, nextStatus);
            }}
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
              <th style={{ width: 72 }}>ID</th>
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
                  <td className="muted">{row.id}</td>
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
                            if (row.collectionSource === SMS_SOURCE) {
                              handleBlockedSmsEditNotice();
                              return;
                            }
                            setEditing(row);
                            setEditingReadOnly(!canEditProject(row));
                          },
                          disabled: row.collectionSource === SMS_SOURCE,
                          title: row.collectionSource === SMS_SOURCE ? SMS_EDIT_BLOCKED_MESSAGE : undefined,
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
                    <span
                      className={
                        row.status === 'Активен'
                          ? 'badge badge--green'
                          : row.status === 'На паузе'
                            ? 'badge badge--orange'
                            : 'badge badge--gray'
                      }
                      style={{ whiteSpace: 'nowrap', cursor: row.status === 'Удалён' || row.collectionSource === SMS_SOURCE ? 'default' : 'pointer' }}
                      title={
                        row.status === 'Удалён'
                          ? 'Проект помечен как удалённый'
                          : row.collectionSource === SMS_SOURCE
                            ? SMS_EDIT_BLOCKED_MESSAGE
                            : 'Нажмите, чтобы переключить статус проекта'
                      }
                      onClick={() => {
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
                              row.collectionSource === SMS_SOURCE
                                ? SMS_EDIT_BLOCKED_MESSAGE
                                : canEdit
                                  ? 'Редактировать проект'
                                  : 'Только просмотр (редактировать свои или через ЛК клиента)'
                            }
                            onClick={() => {
                              if (row.collectionSource === SMS_SOURCE) {
                                handleBlockedSmsEditNotice();
                                return;
                              }
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


