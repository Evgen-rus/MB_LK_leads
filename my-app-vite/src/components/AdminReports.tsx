// Отчёты всех клиентов (для админа)
// Включает столбец "Клиент" с логином и id
import { useEffect, useState } from 'react';
import {
  fetchAdminReports,
  fetchAdminUsers,
  buildLeadsExportUrl,
  createAdminReport,
  type AdminReportItem,
  type UserInfo,
} from '../api';
import DateRangeFilter from './DateRangeFilter';
import ExportDropdown from './ExportDropdown';

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
  async function loadUsers() {
    try {
      const list = await fetchAdminUsers();
      setUsers(list);
      // автоподстановка первого клиента по умолчанию не делаем — надо выбрать явно
    } catch (e) {
      console.error(e);
    }
  }

  async function load(p = page, s = pageSize) {
    try {
      setLoading(true);
      setError(null);
      const offset = (p - 1) * s;
      const resp = await fetchAdminReports({
        offset,
        limit: s,
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

  function parseProjectIdsString(raw?: string | null): number[] | undefined {
    const source = (raw ?? '').trim();
    if (!source) return undefined;
    const parts = source.split(',').map((p) => Number(p.trim())).filter((n) => Number.isFinite(n));
    return parts.length ? parts : undefined;
  }

  function handleCreateReport(format: 'csv' | 'xlsx') {
    if (userIdFilter == null) {
      alert('Выберите клиента для отчёта');
      return;
    }
    createAdminReport({
      fromDate: range.from,
      toDate: range.to,
      projectIds: undefined,
      format,
      clientId: userIdFilter,
    })
      .then(() => {
        load(1, pageSize);
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
              load(1, pageSize);
            }}
          >
            <option value="">Выберите клиента</option>
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
        </div>
        <div className="actions" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <ExportDropdown
            buttonText="Отчёт за период"
            onExport={(fmt) => handleCreateReport(fmt)}
          />
          {loading ? <span className="sub">Загрузка…</span> : <span className="sub">Всего отчётов: {total}</span>}
        </div>
      </div>
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>Сформировал</th>
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
              const projectIds = parseProjectIdsString(r.projectIds);
              return (
                <tr key={r.id} className={idx % 2 === 0 ? 'row-alt' : ''}>
                  <td>
                    <div className="name">{r.user.login}</div>
                    <div className="sub">id: {r.user.id}</div>
                  </td>
                  <td>
                    <div className="name">{r.client?.login || '—'}</div>
                    {r.client && <div className="sub">id: {r.client.id}</div>}
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

