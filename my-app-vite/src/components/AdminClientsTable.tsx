import ClientOptions from './ClientOptions';
// Таблица проектов всех клиентов (для админа)
// Включает столбец "Клиент" и кликабельный dropdown для статуса отгрузки
import { useEffect, useMemo, useState, useCallback } from 'react';
import type { CollectionSource } from '../types/project';
import {
  fetchAdminProjects,
  fetchAdminUsers,
  type AdminProject,
  type UserInfo,
} from '../api';
import AdminEditProjectModal from './AdminEditProjectModal';
import { getValidTokenFromStorage, getUserIdFromToken } from '../utils/jwt';
import DateTimeCompact from './DateTimeCompact';

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

function AdminClientsTable() {
  const [rows, setRows] = useState<AdminProject[]>([]);
  const [users, setUsers] = useState<UserInfo[]>([]);
  const [search, setSearch] = useState<string>('');
  const [status, setStatus] = useState<'Все' | 'Активен' | 'На паузе'>('Все');
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [typeFilter, setTypeFilter] = useState<'Все' | CollectionSource>('Все');
  const [userIdFilter, setUserIdFilter] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [total, setTotal] = useState(0);
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [editing, setEditing] = useState<AdminProject | null>(null);
  const [editingReadOnly, setEditingReadOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  // id текущего админа из токена — разрешаем редактировать только проекты, созданные этим пользователем
  const adminUserId = useMemo(() => {
    const token = getValidTokenFromStorage();
    return getUserIdFromToken(token);
  }, []);

  async function loadUsers() {
    try {
      const list = await fetchAdminUsers();
      setUsers(list);
    } catch (err: unknown) {
      console.error(err);
      setError(getErrorMessage(err, 'Не удалось загрузить список клиентов'));
    }
  }

  // Не привязываем к state page/pageSize, чтобы клики пагинации не перезапускали load(1)
  const load = useCallback(
    async (p: number, s = pageSize, q = search, userId: number | null = userIdFilter, withDeleted = includeDeleted) => {
      try {
        const offset = (p - 1) * s;
        const resp = await fetchAdminProjects({
          offset,
          limit: s,
          q: q.trim() || undefined,
          userId: userId ?? undefined,
          includeDeleted: withDeleted,
        });
        setRows(resp.items);
        setTotal(resp.total);
      } catch (err: unknown) {
        console.error(err);
        setError(getErrorMessage(err, 'Не удалось загрузить проекты'));
      }
    },
    [pageSize, search, userIdFilter, includeDeleted],
  );

  useEffect(() => {
    loadUsers();
    load(1, pageSize);
  }, [load, pageSize]);

  useEffect(() => {
    const h = () => load(page, pageSize, search, userIdFilter, includeDeleted);
    window.addEventListener('admin-projects-refresh', h);
    return () => window.removeEventListener('admin-projects-refresh', h);
  }, [load, page, pageSize, search, userIdFilter, includeDeleted]);

  const filteredRows = useMemo<AdminProject[]>(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((row) => {
      const matchesStatus = status === 'Все' ? true : row.status === status;
      const matchesType = typeFilter === 'Все' ? true : row.collectionSource === typeFilter;
      const nameHit = row.name.toLowerCase().includes(q);
      const idHit = String(row.id).includes(q);
      const userLoginHit = (row.user.name || row.user.login).toLowerCase().includes(q);
      const userIdHit = String(row.user.id).includes(q);
      const matchesQuery = q === '' ? true : (nameHit || idHit || userLoginHit || userIdHit);
      return matchesStatus && matchesType && matchesQuery;
    });
  }, [rows, search, status, typeFilter]);

  const availableTypes = useMemo<CollectionSource[]>(() => {
    const set = new Set<CollectionSource>();
    rows.forEach(r => set.add(r.collectionSource));
    return Array.from(set);
  }, [rows]);

  const filteredIds = useMemo<number[]>(() => filteredRows.map(r => r.id), [filteredRows]);
  const allOnPageSelected = filteredIds.length > 0 && filteredIds.every(id => selectedIds.includes(id));

  function toggleRow(id: number) {
    setSelectedIds((prev) => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  }

  function toggleAllOnPage() {
    setSelectedIds((prev) => {
      if (allOnPageSelected) {
        return prev.filter(id => !filteredIds.includes(id));
      }
      const union = new Set([...prev, ...filteredIds]);
      return Array.from(union);
    });
  }

  return (
    <div className="table-card">
      <div className="table-toolbar">
        <div className="filters">
          <input
            type="search"
            placeholder="Поиск по названию/ID/клиенту"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                setPage(1);
                load(1, pageSize, (e.target as HTMLInputElement).value, userIdFilter, includeDeleted);
              }
            }}
          />
          <select
            value={userIdFilter ?? ''}
            onChange={(e) => {
              const val = e.target.value ? Number(e.target.value) : null;
              setUserIdFilter(val);
              setPage(1);
              load(1, pageSize, search, val, includeDeleted);
            }}
          >
            <option value="">Все клиенты</option>
            <ClientOptions clients={users.map((user) => ({ ...user, name: user.login }))} />
          </select>
          <select value={status} onChange={(e) => setStatus(e.target.value as 'Все' | 'Активен' | 'На паузе')}>
            <option value="Все">Все статусы проекта</option>
            <option value="Активен">Активен</option>
            <option value="На паузе">На паузе</option>
          </select>
          <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value as 'Все' | CollectionSource)}>
            <option value="Все">Все источники</option>
            {availableTypes.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <label className="sub" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <input
              type="checkbox"
              checked={includeDeleted}
              onChange={(e) => {
                const val = e.target.checked;
                setIncludeDeleted(val);
                setPage(1);
                load(1, pageSize, search, userIdFilter, val);
              }}
            />
            Показывать удалённые
          </label>
        </div>
        <div className="actions">
          <span className="sub">Всего: {total}</span>
        </div>
      </div>
      {error && (
        <div className="sub" style={{ color: '#d00', margin: '8px 16px' }}>
          {error}
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
              <th style={{ width: 32 }}>
                <input
                  type="checkbox"
                  checked={allOnPageSelected}
                  onChange={toggleAllOnPage}
                />
              </th>
              <th>Клиент</th>
              <th>Название</th>
              <th>Статус проекта</th>
              <th>Источник сбора</th>
              <th>Лимит</th>
              <th>Сегодня</th>
              <th>Всего</th>
              <th>Дни</th>
              <th>Доменов</th>
              <th>Создан</th>
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
                    onChange={() => toggleRow(row.id)}
                  />
                </td>
                <td>
                  <div className="name">{row.user.name || row.user.login}</div>
                  <div className="sub">id: {row.user.id}</div>
                </td>
                <td>
                    <div className="name">{row.name}</div>
                    <div className="sub">id: {row.id}</div>
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
                    style={{ whiteSpace: 'nowrap' }}
                  >
                    {row.status}
                  </span>
                </td>
                <td>{row.collectionSource}</td>
                <td>{row.dataLimit}</td>
                <td>{row.numbersToday}</td>
                <td>{row.numbersTotal}</td>
                <td className="muted">{row.daysReceived}</td>
                <td>{row.sourcesCount}</td>
                <td className="muted"><DateTimeCompact value={row.createdAt} /></td>
                <td>
                  {(() => {
                    const canEdit = adminUserId != null && row.user?.id === adminUserId;
                    return (
                      <>
                        <button
                          className="icon-btn"
                          title={canEdit ? 'Редактировать' : 'Только просмотр (редактировать свои или через ЛК клиента)'}
                          onClick={() => {
                            setEditing(row);
                            setEditingReadOnly(!canEdit);
                          }}
                        >
                          ⚙️
                        </button>
                      </>
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

      {editing && (
        <AdminEditProjectModal
          project={editing}
          readOnly={editingReadOnly}
          onClose={() => {
            setEditing(null);
            setEditingReadOnly(false);
          }}
          onSubmit={async (updated) => {
            setRows(prev => prev.map(p => p.id === updated.id ? updated : p));
            window.dispatchEvent(new CustomEvent('admin-projects-refresh'));
            setEditing(null);
            setEditingReadOnly(false);
          }}
        />
      )}
    </div>
  );
}

export default AdminClientsTable;

