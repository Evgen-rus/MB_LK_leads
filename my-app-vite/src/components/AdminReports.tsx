// Отчёты всех клиентов (для админа)
// Включает столбец "Клиент" с логином и id
import { useEffect, useState } from 'react';
import { fetchAdminReports, fetchAdminUsers, buildLeadsExportUrl, createAdminReport, type AdminReportItem, type UserInfo } from '../api';
import DateRangeFilter from './DateRangeFilter';

function parseProjectIds(projectIds?: string | null): number[] | undefined {
  if (!projectIds) return undefined;
  const parts = projectIds.split(',').map(p => p.trim()).filter(Boolean);
  if (!parts.length) return undefined;
  const nums = parts.map(p => Number(p)).filter(n => Number.isFinite(n));
  return nums.length ? nums : undefined;
}

function AdminReports() {
  const [users, setUsers] = useState<UserInfo[]>([]);
  const [userIdFilter, setUserIdFilter] = useState<number | null>(null);
  const [items, setItems] = useState<AdminReportItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [total, setTotal] = useState(0);
  const [range, setRange] = useState<{ from: string; to: string }>(() => {
    const today = new Date().toISOString().slice(0, 10);
    return { from: today, to: today };
  });
  const [projectIdsInput, setProjectIdsInput] = useState('');

  async function loadUsers() {
    try {
      const list = await fetchAdminUsers();
      setUsers(list);
    } catch (e) {
      console.error(e);
    }
  }

  async function load(p = page, s = pageSize, userId: number | null = userIdFilter) {
    try {
      setLoading(true);
      setError(null);
      const offset = (p - 1) * s;
      const resp = await fetchAdminReports({
        offset,
        limit: s,
        userId: userId ?? undefined,
      });
      setItems(resp.items);
      setTotal(resp.total);
    } catch (e: any) {
      setError(e?.message || 'Не удалось загрузить отчёты');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadUsers();
    load(1);
  }, []);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  function parseProjectIds(): number[] | undefined {
    const raw = projectIdsInput.trim();
    if (!raw) return undefined;
    const parts = raw.split(',').map((p) => Number(p.trim())).filter((n) => Number.isFinite(n));
    return parts.length ? parts : undefined;
  }

  function handleCreateReport() {
    if (userIdFilter == null) {
      alert('Выберите клиента для отчёта');
      return;
    }
    const projIds = parseProjectIds();
    createAdminReport({
      fromDate: range.from,
      toDate: range.to,
      projectIds: projIds,
      format: 'csv',
    })
      .then(() => {
        load(1, pageSize, userIdFilter);
        alert('Запрос на отчёт создан. Скачайте файл в списке ниже после готовности.');
      })
      .catch((e: any) => {
        alert(e?.message || 'Не удалось создать отчёт');
      });
  }

  return (
    <div className="table-card">
      <div className="table-toolbar">
        <div className="filters">
          <select
            value={userIdFilter ?? ''}
            onChange={(e) => {
              const val = e.target.value ? Number(e.target.value) : null;
              setUserIdFilter(val);
              setPage(1);
              load(1, pageSize, val);
            }}
          >
            <option value="">Все клиенты</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>
                {u.login} (id: {u.id})
              </option>
            ))}
          </select>
          <DateRangeFilter
            from={range.from}
            to={range.to}
            onChange={(r) => setRange(r)}
          />
          <input
            type="text"
            placeholder="ID проектов через запятую (опционально)"
            value={projectIdsInput}
            onChange={(e) => setProjectIdsInput(e.target.value)}
            style={{ minWidth: 240 }}
          />
        </div>
        <div className="actions" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button
            type="button"
            className="btn btn--secondary"
            onClick={handleCreateReport}
          >
            Сформировать отчёт по клиенту
          </button>
          {loading ? (
            <span className="sub">Загрузка…</span>
          ) : (
            <span className="sub">Всего отчётов: {total}</span>
          )}
        </div>
      </div>
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>Клиент</th>
              <th>Дата создания</th>
              <th>Период</th>
              <th>Формат</th>
              <th>Проекты</th>
              <th>Скачать</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && !loading && !error && (
              <tr>
                <td colSpan={6} style={{ textAlign: 'center', padding: 16, color: '#666' }}>
                  Отчётов пока нет.
                </td>
              </tr>
            )}
            {error && (
              <tr>
                <td colSpan={6} style={{ color: '#d00', padding: 16 }}>
                  {error}
                </td>
              </tr>
            )}
            {items.map((r, idx) => {
              const projectIds = parseProjectIds(r.projectIds);
              return (
                <tr key={r.id} className={idx % 2 === 0 ? 'row-alt' : ''}>
                  <td>
                    <div className="name">{r.user.login}</div>
                    <div className="sub">id: {r.user.id}</div>
                  </td>
                  <td className="muted" style={{ whiteSpace: 'nowrap' }}>{r.createdAt}</td>
                  <td className="muted">{r.fromDate} — {r.toDate}</td>
                  <td className="muted" style={{ textTransform: 'uppercase' }}>{r.format}</td>
                  <td className="muted">
                    {projectIds && projectIds.length
                      ? `${projectIds.length} шт.`
                      : 'Все проекты'}
                  </td>
                  <td>
                    <button
                      className="btn btn--secondary"
                      onClick={() => {
                        const url = buildLeadsExportUrl({
                          projectIds,
                          fromDate: r.fromDate,
                          toDate: r.toDate,
                          format: (r.format || 'csv') as 'csv' | 'xlsx',
                          source: 'reports',
                        });
                        window.open(url, '_blank');
                      }}
                    >
                      Скачать
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="table-footer">
        Показано {items.length} из {total}
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
    </div>
  );
}

export default AdminReports;

