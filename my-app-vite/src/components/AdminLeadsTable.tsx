// Таблица лидов всех клиентов (для админа)
// Включает столбец "Клиент" с названием и id
import { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { fetchAdminLeads, fetchAdminUsers, fetchAdminProjects, downloadLeadsExport, type AdminLead, type UserInfo, type AdminProject } from '../api';
import ExportDropdown from './ExportDropdown';
import DateRangeFilter from './DateRangeFilter';
import FilterDropdown from './FilterDropdown';
import DateTimeCompact from './DateTimeCompact';
import { formatProjectNameForDisplay, formatSourceTextForDisplay, getSourceCodeFilterOptions, toDisplaySourceCode } from '../utils/sourceCodeDisplay';

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return formatSourceTextForDisplay(msg);
  }
  return formatSourceTextForDisplay(fallback);
}

function isPixelLead(row: Pick<AdminLead, 'lead_source' | 'collection_source'>): boolean {
  return row.lead_source === 'pixel' || row.collection_source === 'Пиксель';
}

function leadCollectionLabel(row: AdminLead): string {
  if (isPixelLead(row)) return 'Пиксель';
  return row.collection_source || '—';
}

function leadChannelLabel(row: AdminLead): string {
  if (isPixelLead(row)) return '—';
  return toDisplaySourceCode(row.source ?? '') || '—';
}

function leadSourceText(row: AdminLead): string {
  if (isPixelLead(row)) return row.pixel_url || row.utm_campaign || '—';
  return formatSourceTextForDisplay(row.utm_campaign ?? '') || '—';
}

function formatDateInput(d: Date) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

const COLLECTION_SOURCE_FILTER_OPTIONS = [
  'Сайты',
  'Звонки',
  'СМС',
  'Ретросайты',
  'Ретрозвонки',
  'Пересечение',
  'Пиксель',
].map((value) => ({ value, label: value }));

type AdminLeadsInitialFilter = {
  clientId?: number;
  projectId?: number;
  from?: string;
  to?: string;
  unlinked?: boolean;
};

function AdminLeadsTable({ initialFilter }: { initialFilter?: AdminLeadsInitialFilter }) {
  const [users, setUsers] = useState<UserInfo[]>([]);
  const [userIdFilter, setUserIdFilter] = useState<number | null>(null);
  const [projects, setProjects] = useState<AdminProject[]>([]);
  const [projectIds, setProjectIds] = useState<number[]>([]);
  const [sources, setSources] = useState<string[]>([]);
  const [collectionSources, setCollectionSources] = useState<string[]>([]);
  const [fromDate, setFromDate] = useState<string>(formatDateInput(new Date()));
  const [toDate, setToDate] = useState<string>(formatDateInput(new Date()));
  const [rows, setRows] = useState<AdminLead[]>([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [unlinkedOnly, setUnlinkedOnly] = useState(Boolean(initialFilter?.unlinked));
  const initialApplied = useRef(false);
  const initialProjectId = useRef<number | undefined>(initialFilter?.projectId);

  const projectNameMap = useMemo(() => {
    const m = new Map<number, string>();
    projects.forEach((p) => m.set(p.id, formatProjectNameForDisplay(p.name)));
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

  // Загрузка страницы; не привязываем к state page, чтобы смена страницы
  // не дергала useEffect с load(1)
  const load = useCallback(async (p: number, s = pageSize) => {
    try {
      setLoading(true);
      const offset = (p - 1) * s;
      const resp = await fetchAdminLeads({
        fromDate,
        toDate,
        userId: userIdFilter ?? undefined,
        projectIds: projectIds.length ? projectIds : undefined,
        sources: sources.length ? sources : undefined,
        collectionSources: collectionSources.length ? collectionSources : undefined,
        unlinked: unlinkedOnly,
        offset,
        limit: s,
      });
      setRows(resp.items);
      setTotal(resp.total);
    } catch (err: unknown) {
      console.error(err);
      setError(getErrorMessage(err, 'Не удалось загрузить лиды'));
    } finally {
      setLoading(false);
    }
  }, [pageSize, userIdFilter, fromDate, toDate, projectIds, sources, collectionSources, unlinkedOnly]);

  useEffect(() => {
    loadUsers();
  }, []);

  useEffect(() => {
    load(1);
  }, [fromDate, toDate, userIdFilter, projectIds, sources, collectionSources, load]);

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
    if (initialFilter.unlinked) setUnlinkedOnly(true);
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

  const handleExport = async (format: 'csv' | 'xlsx') => {
    if (!userIdFilter) {
      alert('Сначала выберите клиента');
      return;
    }
    await downloadLeadsExport({
      projectIds: projectIds.length ? projectIds : undefined,
      sources: sources.length ? sources : undefined,
      collectionSources: collectionSources.length ? collectionSources : undefined,
      fromDate,
      toDate,
      format,
      clientId: userIdFilter ?? undefined,
      source: 'leads',
    });
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
            options={(userIdFilter ? projects : []).map((p) => ({ value: String(p.id), label: formatProjectNameForDisplay(p.name) }))}
            selected={projectIds.map(String)}
            allLabel="Все проекты"
            disabled={!userIdFilter}
            onApply={(vals) => {
              setProjectIds(vals.length === projects.length ? [] : vals.map(Number));
              setPage(1);
            }}
          />

          {/* Фильтр по каналам */}
          <FilterDropdown
            label="Каналы"
            options={getSourceCodeFilterOptions(sourcesList)}
            selected={sources}
            allLabel="Все каналы"
            onApply={(vals) => {
              setSources(vals);
              setPage(1);
            }}
          />
          <FilterDropdown
            label="Источник сбора"
            options={COLLECTION_SOURCE_FILTER_OPTIONS}
            selected={collectionSources}
            allLabel="Все источники"
            emptySelectionShowsAll={false}
            onApply={(vals) => {
              setCollectionSources(vals);
              setPage(1);
            }}
          />
          <label className="dashboard-toggle">
            <input
              type="checkbox"
              checked={unlinkedOnly}
              onChange={(e) => {
                setUnlinkedOnly(e.target.checked);
                if (e.target.checked) {
                  setUserIdFilter(null);
                  setProjectIds([]);
                }
                setPage(1);
              }}
            />
            Без привязки
          </label>
        </div>
        <div className="actions" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {loading ? <span className="sub">Загрузка…</span> : <span className="sub">Итого данных: {total}</span>}
          <ExportDropdown onExport={handleExport} />
        </div>
      </div>
      {error && (
        <div className="sub" style={{ color: '#d00', margin: '8px 16px' }}>
          {error}
        </div>
      )}
      <div className="table-toolbar" style={{ borderTop: 'none' }}>
      </div>
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
        <table className="table table--leads">
          <thead>
            <tr>
              <th>Дата</th>
              <th>Телефон</th>
              <th>Источник сбора</th>
              <th>Канал</th>
              <th>Источники</th>
              <th>Проект</th>
              <th>lk id</th>
              <th>ext_id</th>
              <th>Клиент</th>
            </tr>
          </thead>
          <tbody>
            {!userIdFilter && !loading && (
              <tr>
                <td colSpan={9} className="muted" style={{ padding: 16, textAlign: 'center' }}>
                  Выберите клиента, чтобы увидеть идентификации.
                </td>
              </tr>
            )}
            {userIdFilter && !loading && rows.length === 0 && (
              <tr>
                <td colSpan={9} className="muted" style={{ padding: 16, textAlign: 'center' }}>
                  Данных за выбранный период нет.
                </td>
              </tr>
            )}
            {rows.map((r) => (
              <tr key={r.lk_id || `${r.ext_id}-${r.phone}`} style={{ borderBottom: '1px solid #ececf2' }}>
                <td><DateTimeCompact value={r.created_at} /></td>
                <td>{r.phone}</td>
                <td className="muted">{leadCollectionLabel(r)}</td>
                <td className="muted">{leadChannelLabel(r)}</td>
                <td className="muted" title={leadSourceText(r)} style={{ maxWidth: 360, overflowWrap: 'anywhere' }}>
                  {leadSourceText(r)}
                </td>
                <td>
                  <div className="name">
                    {formatProjectNameForDisplay(r.project_name ?? (r.project_id != null ? projectNameMap.get(r.project_id) : undefined) ?? '—')}
                    {r.project_id != null ? (
                      <span className="project-id-badge">
                        id{r.project_id}
                      </span>
                    ) : null}
                  </div>
                </td>
                <td className="muted">{r.lk_id}</td>
                <td className="muted">{r.ext_id}</td>
                <td>
                  <div className="name">{r.user.name || r.user.login}</div>
                  <div className="sub">id: {r.user.id}</div>
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
    </div>
  );
}

export default AdminLeadsTable;

