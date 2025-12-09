// Таблица лидов всех клиентов (для админа)
// Включает столбец "Клиент" с логином и id
import { useEffect, useMemo, useState } from 'react';
import { fetchAdminLeads, fetchAdminUsers, fetchAdminProjects, buildLeadsExportUrl, type AdminLead, type UserInfo, type AdminProject } from '../api';
import ExportDropdown from './ExportDropdown';
import DateRangeFilter from './DateRangeFilter';
import FilterDropdown from './FilterDropdown';

function formatDateInput(d: Date) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function AdminLeadsTable() {
  const [users, setUsers] = useState<UserInfo[]>([]);
  const [userIdFilter, setUserIdFilter] = useState<number | null>(null);
  const [projects, setProjects] = useState<AdminProject[]>([]);
  const [projectIds, setProjectIds] = useState<number[]>([]);
  const [sources, setSources] = useState<string[]>([]);
  const [fromDate, setFromDate] = useState<string>(formatDateInput(new Date()));
  const [toDate, setToDate] = useState<string>(formatDateInput(new Date()));
  const [rows, setRows] = useState<AdminLead[]>([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [total, setTotal] = useState(0);

  const projectNameMap = useMemo(() => {
    const m = new Map<number, string>();
    projects.forEach((p) => m.set(p.id, p.name));
    return m;
  }, [projects]);

  async function loadUsers() {
    try {
      const list = await fetchAdminUsers();
      setUsers(list);
    } catch (e) {
      console.error(e);
    }
  }

  async function load(p = page, s = pageSize) {
    if (!userIdFilter) {
      // Пока клиент не выбран — таблица пустая
      setRows([]);
      setTotal(0);
      return;
    }
    try {
      setLoading(true);
      const offset = (p - 1) * s;
      const resp = await fetchAdminLeads({
        fromDate,
        toDate,
        userId: userIdFilter ?? undefined,
        projectIds: projectIds.length ? projectIds : undefined,
        sources: sources.length ? sources : undefined,
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
  }, [fromDate, toDate, userIdFilter, projectIds, sources]);

  // При выборе клиента подгружаем его проекты
  useEffect(() => {
    (async () => {
      if (!userIdFilter) {
        setProjects([]);
        setProjectIds([]);
        return;
      }
      try {
        const resp = await fetchAdminProjects({ offset: 0, limit: 10000, userId: userIdFilter });
        setProjects(resp.items);
        setProjectIds([]);
      } catch (e) {
        console.error(e);
      }
    })();
  }, [userIdFilter]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const sourcesList = useMemo(() => {
    const preset = ['B1', 'B2', 'B3', 'B4'];
    const set = new Set<string>(preset);
    rows.forEach((r) => {
      if (r.source) set.add(r.source);
    });
    return Array.from(set).sort();
  }, [rows]);

  const handleExport = (format: 'csv' | 'xlsx') => {
    const url = buildLeadsExportUrl({
      projectIds: projectIds.length ? projectIds : undefined,
      sources: sources.length ? sources : undefined,
      fromDate,
      toDate,
      format,
    });
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
            <option value="">Выберите клиента…</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>
                {u.login} (id: {u.id})
              </option>
            ))}
          </select>

          {/* Фильтр по проектам клиента (мультивыбор) */}
          <FilterDropdown
            label="Проекты"
            options={(userIdFilter ? projects : []).map((p) => ({ value: String(p.id), label: `${p.id} — ${p.name}` }))}
            selected={projectIds.map(String)}
            placeholder="Пусто = все проекты"
            allLabel="Все проекты"
            disabled={!userIdFilter}
            onApply={(vals) => {
              setProjectIds(vals.map(Number));
              setPage(1);
            }}
          />

          {/* Фильтр по источникам */}
          <FilterDropdown
            label="Источники"
            options={sourcesList.map((s) => ({ value: s, label: s }))}
            selected={sources}
            placeholder="Пусто = все источники"
            allLabel="Все источники"
            onApply={(vals) => {
              setSources(vals);
              setPage(1);
            }}
          />
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
              <th>Клиент</th>
              <th>ext_id</th>
              <th>Проект</th>
              <th>Источник</th>
              <th>Дата</th>
              <th>Телефон</th>
              <th>UTM_CAMPAIGN</th>
            </tr>
          </thead>
          <tbody>
            {!userIdFilter && !loading && (
              <tr>
                <td colSpan={7} className="muted" style={{ padding: 16, textAlign: 'center' }}>
                  Выберите клиента, чтобы увидеть идентификации.
                </td>
              </tr>
            )}
            {userIdFilter && !loading && rows.length === 0 && (
              <tr>
                <td colSpan={7} className="muted" style={{ padding: 16, textAlign: 'center' }}>
                  Данных за выбранный период нет.
                </td>
              </tr>
            )}
            {rows.map((r, idx) => (
              <tr key={r.ext_id} className={idx % 2 === 0 ? 'row-alt' : ''}>
                <td>
                  <div className="name">{r.user.login}</div>
                  <div className="sub">id: {r.user.id}</div>
                </td>
                <td className="muted">{r.ext_id}</td>
                <td>
                  <div className="name">{r.project_name ?? projectNameMap.get(r.project_id) ?? '—'}</div>
                  <div className="sub">id: {r.project_id}</div>
                </td>
                <td className="muted">{r.source ?? ''}</td>
                <td>{r.imported_at}</td>
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

