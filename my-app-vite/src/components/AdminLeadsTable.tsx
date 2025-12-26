// Таблица лидов всех клиентов (для админа)
// Включает столбец "Клиент" с названием и id
import { useEffect, useMemo, useRef, useState } from 'react';
import { fetchAdminLeads, fetchAdminUsers, fetchAdminProjects, buildLeadsExportUrl, createAdminReport, type AdminLead, type UserInfo, type AdminProject } from '../api';
import ExportDropdown from './ExportDropdown';
import DateRangeFilter from './DateRangeFilter';
import FilterDropdown from './FilterDropdown';

function formatDateInput(d: Date) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

type AdminLeadsInitialFilter = {
  clientId?: number;
  projectId?: number;
  from?: string;
  to?: string;
};

function AdminLeadsTable({ initialFilter }: { initialFilter?: AdminLeadsInitialFilter }) {
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
  const initialApplied = useRef(false);
  const initialProjectId = useRef<number | undefined>(initialFilter?.projectId);

  const projectNameMap = useMemo(() => {
    const m = new Map<number, string>();
    projects.forEach((p) => m.set(p.id, p.name));
    return m;
  }, [projects]);

  // По умолчанию — все проекты клиента
  useEffect(() => {
    if (projects.length && projectIds.length === 0) {
      setProjectIds(projects.map((p) => p.id));
    }
  }, [projects, projectIds.length]);

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
        // Если был prefill с projectId — применяем его один раз после загрузки проектов
        if (!initialApplied.current && initialProjectId.current) {
          setProjectIds([initialProjectId.current]);
          initialApplied.current = true;
        } else if (!initialApplied.current) {
          setProjectIds([]);
        }
      } catch (e) {
        console.error(e);
      }
    })();
  }, [userIdFilter]);

  // Предзаполнение фильтров при переходе из админских «Проектов»
  useEffect(() => {
    if (!initialFilter || initialApplied.current) return;
    const { clientId, projectId, from, to } = initialFilter;
    if (clientId) setUserIdFilter(clientId);
    if (from) setFromDate(from);
    if (to) setToDate(to);
    // projectId применяем после загрузки проектов (в эффекте выше)
    if (projectId) {
      initialProjectId.current = projectId;
    }
  }, [initialFilter, setUserIdFilter, setFromDate, setToDate]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const sourcesList = useMemo(() => {
    const preset = ['B1', 'B2', 'B3', 'B4'];
    const set = new Set<string>(preset);
    rows.forEach((r) => {
      if (r.source) set.add(r.source);
    });
    return Array.from(set).sort();
  }, [rows]);

  // По умолчанию — все источники
  useEffect(() => {
    if (sourcesList.length && sources.length === 0) {
      setSources(sourcesList);
    }
  }, [sourcesList, sources.length]);

  const handleExport = async (format: 'csv' | 'xlsx') => {
    if (!userIdFilter) {
      alert('Сначала выберите клиента');
      return;
    }
    // Логируем экспорт в общие отчёты админа
    try {
      await createAdminReport({
        fromDate,
        toDate,
        projectIds: projectIds.length ? projectIds : undefined,
        format,
        clientId: userIdFilter,
      });
    } catch (e) {
      console.error('Не удалось зафиксировать экспорт отчёта', e);
    }
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
                {u.name || u.login} (id: {u.id})
              </option>
            ))}
          </select>

          {/* Фильтр по проектам клиента (мультивыбор) */}
          <FilterDropdown
            label="Проекты"
            options={(userIdFilter ? projects : []).map((p) => ({ value: String(p.id), label: `${p.id} — ${p.name}` }))}
            selected={projectIds.map(String)}
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
            allLabel="Все источники"
            onApply={(vals) => {
              setSources(vals);
              setPage(1);
            }}
          />
        </div>
        <div className="actions" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {loading ? <span className="sub">Загрузка…</span> : <span className="sub">Итого данных: {total}</span>}
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
            {rows.map((r) => (
              <tr key={r.ext_id} style={{ borderBottom: '1px solid #ececf2' }}>
                <td>
                  <div className="name">{r.user.name || r.user.login}</div>
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

