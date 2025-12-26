// Черный список всех клиентов (для админа)
// Включает столбец "Клиент" с логином и id
import { useEffect, useState, useCallback } from 'react';
import { fetchAdminBlacklist, fetchAdminUsers, type AdminBlacklistPhone, type UserInfo } from '../api';

function AdminBlacklist() {
  const [users, setUsers] = useState<UserInfo[]>([]);
  const [userIdFilter, setUserIdFilter] = useState<number | null>(null);
  const [rows, setRows] = useState<AdminBlacklistPhone[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  async function loadUsers() {
    try {
      const list = await fetchAdminUsers();
      setUsers(list);
    } catch (e) {
      console.error(e);
    }
  }

  // Не привязываем к state page/pageSize, чтобы переключение страниц не сбрасывало загрузку
  const fetchPage = useCallback(async (nextPage = page, nextPageSize = pageSize, q = search, userId: number | null = userIdFilter) => {
    try {
      setLoading(true);
      const offset = (nextPage - 1) * nextPageSize;
      const resp = await fetchAdminBlacklist({
        offset,
        limit: nextPageSize,
        q: q.trim() || undefined,
        userId: userId ?? undefined,
      });
      setRows(resp.items);
      setTotal(resp.total);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [pageSize, search, userIdFilter]);

  useEffect(() => {
    loadUsers();
    fetchPage(1);
  }, [fetchPage]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="table-card">
      <div className="table-toolbar">
        <div className="filters">
          <input
            type="search"
            placeholder="Найти телефон"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                setPage(1);
                fetchPage(1, pageSize, (e.target as HTMLInputElement).value, userIdFilter);
              }
            }}
          />
          <select
            value={userIdFilter ?? ''}
            onChange={(e) => {
              const val = e.target.value ? Number(e.target.value) : null;
              setUserIdFilter(val);
              setPage(1);
              fetchPage(1, pageSize, search, val);
            }}
          >
            <option value="">Все клиенты</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name || u.login} (id: {u.id})
                </option>
              ))}
          </select>
        </div>
        <div className="actions">
          <span className="sub">Всего: {total}</span>
        </div>
      </div>

      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>Клиент</th>
              <th>Телефон</th>
              <th>Дата добавления</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td className="muted" colSpan={3}>Загрузка...</td></tr>
            ) : rows.length === 0 ? (
              <tr><td className="muted" colSpan={3}>Список пуст</td></tr>
            ) : (
              rows.map((r) => (
                <tr key={r.id}>
                  <td>
                    <div className="name">{r.user.name || r.user.login}</div>
                    <div className="sub">id: {r.user.id}</div>
                  </td>
                  <td className="name" style={{ whiteSpace: 'nowrap' }}>{r.phone}</td>
                  <td className="muted">{r.createdAt}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="table-footer">
        Показано {rows.length} из {total}
        <div className="spacer" />
        <div className="pager">
          <button className="pager__btn" disabled={page <= 1} onClick={() => { const p = Math.max(1, page - 1); setPage(p); fetchPage(p); }}>‹</button>
          <span className="pager__info">{page} / {totalPages}</span>
          <button className="pager__btn" disabled={page >= totalPages} onClick={() => { const p = Math.min(totalPages, page + 1); setPage(p); fetchPage(p); }}>›</button>
          <select className="pager__size" value={pageSize} onChange={(e) => { const s = Number(e.target.value); setPageSize(s); setPage(1); fetchPage(1, s); }}>
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

export default AdminBlacklist;

