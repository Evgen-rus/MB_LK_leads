// Экран «Проекты клиента» для админа.
// Показывает проекты только выбранного клиента в стиле обычной вкладки «Проекты».
import { useEffect, useMemo, useState } from 'react';
import { fetchAdminProjects, updateAdminProject, deleteAdminProject, type AdminProject, type AdminProjectUpdate } from '../api';
import AdminEditProjectModal from './AdminEditProjectModal';
import AdminProjectHistoryModal from './AdminProjectHistoryModal';

type AdminClientProjectsProps = {
  clientId: number;
  clientName: string;
  fromDate: string;
  toDate: string;
  // Количество необработанных изменений по каждому проекту (projectId -> count)
  projectChanges?: Record<number, number>;
  // Количество необработанных созданий по каждому проекту (projectId -> count)
  projectCreates?: Record<number, number>;
};

function AdminClientProjects({ clientId, clientName, fromDate, toDate, projectChanges, projectCreates }: AdminClientProjectsProps) {
  const [rows, setRows] = useState<AdminProject[]>([]);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<AdminProject | null>(null);
  const [historyFor, setHistoryFor] = useState<AdminProject | null>(null);
  const [includeDeleted, setIncludeDeleted] = useState(false);

  async function load(p = page, s = pageSize, q = search, from = fromDate, to = toDate, withDeleted = includeDeleted) {
    try {
      setLoading(true);
      setError(null);
      const offset = (p - 1) * s;
      const resp = await fetchAdminProjects({
        offset,
        limit: s,
        q: q.trim() || undefined,
        userId: clientId,
        fromDate: from,
        toDate: to,
        includeDeleted: withDeleted,
      });
      setRows(resp.items);
      setTotal(resp.total);
    } catch (e: any) {
      console.error(e);
      setError(e?.message || 'Не удалось загрузить проекты клиента');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(1, pageSize, search, fromDate, toDate, includeDeleted);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId, fromDate, toDate, includeDeleted]);

  const filteredRows = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((row) => {
      const nameHit = row.name.toLowerCase().includes(q);
      const idHit = String(row.id).includes(q);
      return nameHit || idHit;
    });
  }, [rows, search]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  function calcRemaining(p: AdminProject): number {
    const limit = p.dataLimit || 0;
    const used = p.numbersTotal || 0;
    return Math.max(0, limit - used);
  }

  async function handleDelete(id: number) {
    if (!window.confirm(`Удалить проект ${id}?`)) return;
    try {
      await deleteAdminProject(id);
      setRows((prev) => prev.filter((p) => p.id !== id));
    } catch (e) {
      console.error(e);
      alert('Ошибка при удалении проекта');
    }
  }

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
        days: ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'] as any,
        ...patch,
      };
      const updated = await updateAdminProject(project.id, payload);
      setRows((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
    } catch (e) {
      console.error(e);
      alert('Ошибка при обновлении проекта');
    }
  }

  // Переключение статуса проекта (Активен <-> На паузе) для админского экрана «Проекты клиента».
  async function handleToggleStatus(project: AdminProject) {
    if (project.status === 'Удалён') return;
    const nextStatus = project.status === 'Активен' ? 'На паузе' : 'Активен';
    await applyUpdate(project, { status: nextStatus });
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
                load(1, pageSize, (e.target as HTMLInputElement).value, fromDate, toDate, includeDeleted);
              }
            }}
          />
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
          <span className="sub">
            Проекты клиента: {clientName} (id: {clientId}) — всего {total}
          </span>
        </div>
      </div>

      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>Название проекта</th>
              <th>Статус проекта</th>
              <th>Лимит</th>
              <th>Остаток</th>
              <th>Источник</th>
              <th>Номеров за период</th>
              <th>Номеров всего</th>
              <th>Действия</th>
            </tr>
          </thead>
          <tbody>
            {error && (
              <tr>
                <td colSpan={8} style={{ color: '#d00', padding: 16 }}>
                  {error}
                </td>
              </tr>
            )}
            {!error && loading && (
              <tr>
                <td colSpan={8} className="muted" style={{ padding: 16 }}>
                  Загрузка проектов…
                </td>
              </tr>
            )}
            {!error && !loading && filteredRows.length === 0 && (
              <tr>
                <td colSpan={8} className="muted" style={{ padding: 16 }}>
                  Проекты клиента не найдены.
                </td>
              </tr>
            )}
            {!error &&
              !loading &&
              filteredRows.map((row, idx) => (
                <tr key={row.id} className={idx % 2 === 0 ? 'row-alt' : ''}>
                  <td>
                    <div className="name">{row.name}</div>
                    <div className="sub muted">ID: {row.id}</div>
                  {(projectChanges?.[row.id] || projectCreates?.[row.id]) && (
                    <div className="sub" style={{ marginTop: 2, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      {!!projectChanges?.[row.id] && projectChanges[row.id]! > 0 && (
                        <span
                          className="badge badge--orange"
                          style={{ fontWeight: 500 }}
                        >
                          Изменения: {projectChanges[row.id]}
                        </span>
                      )}
                      {!!projectCreates?.[row.id] && projectCreates[row.id]! > 0 && (
                        <span
                          className="badge badge--gray"
                          style={{ fontWeight: 500 }}
                        >
                          Создания: {projectCreates[row.id]}
                        </span>
                      )}
                    </div>
                  )}
                  </td>
                  <td>
                    <span
                      className={
                        row.status === 'Активен'
                          ? 'badge badge--green'
                          : row.status === 'На паузе'
                            ? 'badge badge--orange'
                            : 'badge badge--gray'
                      }
                      style={{ whiteSpace: 'nowrap', cursor: row.status === 'Удалён' ? 'default' : 'pointer' }}
                      title="Нажмите, чтобы переключить статус проекта"
                      onClick={() => {
                        if (row.status === 'Удалён') return;
                        handleToggleStatus(row);
                      }}
                    >
                      {row.status}
                    </span>
                  </td>
                  <td>{row.dataLimit}</td>
                  <td>{calcRemaining(row)}</td>
                  <td>{row.collectionSource}</td>
                  <td>{row.numbersPeriod ?? row.numbersToday}</td>
                  <td>{row.numbersTotal}</td>
                  <td>
                    <button
                      className="icon-btn"
                      title="Редактировать проект"
                      onClick={() => setEditing(row)}
                    >
                      ⚙️
                    </button>
                    <button
                      className="icon-btn"
                      title="История изменений"
                      onClick={() => setHistoryFor(row)}
                    >
                      🕘
                    </button>
                    <button
                      className="icon-btn"
                      title="Удалить проект"
                      onClick={() => handleDelete(row.id)}
                    >
                      🗑️
                    </button>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      <div className="table-footer">
        Показано {filteredRows.length} из {total}
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
          onClose={() => setEditing(null)}
          onSubmit={(updated) => {
            setRows((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
            setEditing(null);
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


