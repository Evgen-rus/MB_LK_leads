// Таблица лидов всех клиентов (для админа)
// Включает столбец "Клиент" с логином и id
import { useEffect, useState } from 'react';
import { fetchAdminLeads, fetchAdminUsers, buildLeadsExportUrl, type AdminLead, type UserInfo } from '../api';
import ExportDropdown from './ExportDropdown';
import DateRangeFilter from './DateRangeFilter';

function formatDateInput(d: Date) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function AdminLeadsTable() {
  const [users, setUsers] = useState<UserInfo[]>([]);
  const [userIdFilter, setUserIdFilter] = useState<number | null>(null);
  const [fromDate, setFromDate] = useState<string>(formatDateInput(new Date()));
  const [toDate, setToDate] = useState<string>(formatDateInput(new Date()));
  const [rows, setRows] = useState<AdminLead[]>([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [total, setTotal] = useState(0);

  async function loadUsers() {
    try {
      const list = await fetchAdminUsers();
      setUsers(list);
    } catch (e) {
      console.error(e);
    }
  }

  async function load(p = page, s = pageSize) {
    try {
      setLoading(true);
      const offset = (p - 1) * s;
      const resp = await fetchAdminLeads({
        fromDate,
        toDate,
        userId: userIdFilter ?? undefined,
        offset,
        limit: s,
      });
      setRows(resp.items);
      setTotal(resp.total);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadUsers();
  }, []);

  useEffect(() => {
    load(1);
  }, [fromDate, toDate, userIdFilter]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const handleExport = (format: 'csv' | 'xlsx') => {
    // Экспорт через стандартный эндпоинт (все проекты, если не фильтруем)
    const url = buildLeadsExportUrl({ projectIds: undefined, fromDate, toDate, format });
    window.open(url, '_blank');
  };

  return (
    <div className="table-card">
      <div className="table-toolbar">
        <div className="filters" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {/* Выбор дат слева, как и в пользовательском ЛК */}
          <DateRangeFilter
            from={fromDate}
            to={toDate}
            onChange={({ from, to }) => {
              setFromDate(from);
              setToDate(to);
            }}
          />

          {/* Фильтр по клиенту */}
          <select
            value={userIdFilter ?? ''}
            onChange={(e) => {
              const val = e.target.value ? Number(e.target.value) : null;
              setUserIdFilter(val);
              setPage(1);
            }}
          >
            <option value="">Все клиенты</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>
                {u.login} (id: {u.id})
              </option>
            ))}
          </select>
        </div>
        <div className="actions" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {loading ? <span className="sub">Загрузка…</span> : <span className="sub">Найдено: {total}</span>}
          <ExportDropdown onExport={handleExport} />
        </div>
      </div>
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>ext_id</th>
              <th>Клиент</th>
              <th>project_id</th>
              <th>Дата</th>
              <th>Телефон</th>
              <th>UTM_CAMPAIGN</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, idx) => (
              <tr key={r.ext_id} className={idx % 2 === 0 ? 'row-alt' : ''}>
                <td className="muted">{r.ext_id}</td>
                <td>
                  <div className="name">{r.user.login}</div>
                  <div className="sub">id: {r.user.id}</div>
                </td>
                <td className="muted">{r.project_id}</td>
                <td>{r.created_at}</td>
                <td>{r.phone}</td>
                <td className="muted">{r.utm_campaign ?? ''}</td>
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
    </div>
  );
}

export default AdminLeadsTable;

