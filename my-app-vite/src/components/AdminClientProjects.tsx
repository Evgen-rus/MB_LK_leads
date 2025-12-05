// Экран «Проекты клиента» для админа.
// Показывает проекты только выбранного клиента в стиле обычной вкладки «Проекты».
import { useEffect, useMemo, useState } from 'react';
import { fetchAdminProjects, updateAdminProject, deleteAdminProject, type AdminProject, type AdminProjectUpdate } from '../api';
import AdminEditProjectModal from './AdminEditProjectModal';

type AdminClientProjectsProps = {
  clientId: number;
  clientName: string;
  // Количество необработанных изменений по каждому проекту (projectId -> count)
  projectChanges?: Record<number, number>;
};

function AdminClientProjects({ clientId, clientName, projectChanges }: AdminClientProjectsProps) {
  const [rows, setRows] = useState<AdminProject[]>([]);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<AdminProject | null>(null);

  async function load(p = page, s = pageSize, q = search) {
    try {
      setLoading(true);
      setError(null);
      const offset = (p - 1) * s;
      const resp = await fetchAdminProjects({
        offset,
        limit: s,
        q: q.trim() || undefined,
        userId: clientId,
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
    load(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientId]);

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
                load(1, pageSize, (e.target as HTMLInputElement).value);
              }
            }}
          />
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
              <th>Сегодня</th>
              <th>Всего</th>
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
                    {!!projectChanges?.[row.id] && projectChanges[row.id]! > 0 && (
                      <div className="sub" style={{ marginTop: 2 }}>
                        <span
                          className="badge badge--orange"
                          style={{ fontWeight: 500 }}
                        >
                          Изменения: {projectChanges[row.id]}
                        </span>
                      </div>
                    )}
                  </td>
                  <td>
                    <span
                      className={row.status === 'Активен' ? 'badge badge--green' : 'badge badge--orange'}
                      style={{ whiteSpace: 'nowrap' }}
                    >
                      {row.status}
                    </span>
                  </td>
                  <td>{row.dataLimit}</td>
                  <td>{calcRemaining(row)}</td>
                  <td>{row.collectionSource}</td>
                  <td>{row.numbersToday}</td>
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
    </div>
  );
}

export default AdminClientProjects;


