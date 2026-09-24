// Таблица лидов с фильтрами по проекту и дате
import { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import type { Project } from '../types/project';
import { fetchLeads, downloadLeadsExport, type Lead } from '../api';
import ExportDropdown from './ExportDropdown';
import FilterDropdown from './FilterDropdown';
import DateRangeFilter from './DateRangeFilter';
import DateTimeCompact from './DateTimeCompact';
import { formatProjectNameForDisplay, formatSourceTextForDisplay, getSourceCodeFilterOptions, toDisplaySourceCode } from '../utils/sourceCodeDisplay';

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return formatSourceTextForDisplay(msg);
  }
  return formatSourceTextForDisplay(fallback);
}

function isPixelLead(row: Pick<Lead, 'lead_source' | 'collection_source'>): boolean {
  return row.lead_source === 'pixel' || row.collection_source === 'Пиксель';
}

function leadCollectionLabel(row: Lead): string {
  if (isPixelLead(row)) return 'Пиксель';
  return row.collection_source || '—';
}

function leadChannelLabel(row: Lead): string {
  if (isPixelLead(row)) return '—';
  return toDisplaySourceCode(row.source ?? '') || '—';
}

function leadSourceText(row: Lead): string {
  if (isPixelLead(row)) return row.pixel_url || row.utm_campaign || '—';
  return formatSourceTextForDisplay(row.utm_campaign ?? '') || '—';
}

type Props = {
  projects: Project[];
  initialFilter?: { projectId?: number; from?: string; to?: string };
};

const SEARCH_DEBOUNCE_MS = 400;
const COLLECTION_SOURCE_FILTER_OPTIONS = [
  'Сайты',
  'Звонки',
  'СМС',
  'Ретросайты',
  'Ретрозвонки',
  'Пересечение',
  'Пиксель',
].map((value) => ({ value, label: value }));

// Формат для value инпута даты (YYYY-MM-DD)
function formatDateInput(d: Date) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function LeadsTable({ projects, initialFilter }: Props) {
  const [projectIds, setProjectIds] = useState<number[]>([]);
  const [sources, setSources] = useState<string[]>([]);
  const [collectionSources, setCollectionSources] = useState<string[]>([]);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [fromDate, setFromDate] = useState<string>(formatDateInput(new Date()));
  const [toDate, setToDate] = useState<string>(formatDateInput(new Date()));
  const [rows, setRows] = useState<Lead[]>([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const initialProjectId = useRef<number | undefined>(initialFilter?.projectId);
  const initialProjectApplied = useRef(false);
  const initialDatesApplied = useRef(false);

  const projectNameMap = useMemo(() => {
    const m = new Map<number, string>();
    projects.forEach((p) => m.set(p.id, formatProjectNameForDisplay(p.name)));
    return m;
  }, [projects]);

  // По умолчанию пустой выбор означает "все проекты".
  useEffect(() => {
    if (!projects.length) return;
    if (!initialProjectApplied.current && initialProjectId.current) {
      setProjectIds([initialProjectId.current]);
      initialProjectApplied.current = true;
      return;
    }
    if (!initialProjectApplied.current) {
      initialProjectApplied.current = true;
    }
  }, [projects]);

  // Применяем входные фильтры (переход из таблицы проектов)
  useEffect(() => {
    if (!initialFilter) return;
    // Если пришёл новый projectId или даты — сбрасываем флаги, чтобы применить снова
    initialProjectApplied.current = false;
    initialDatesApplied.current = false;
    const { projectId, from, to } = initialFilter;
    if (projectId) {
      initialProjectId.current = projectId;
    }
    if (!initialDatesApplied.current) {
      if (from) setFromDate(from);
      if (to) setToDate(to);
      initialDatesApplied.current = true;
    }
  }, [initialFilter, setFromDate, setToDate]);

  // Загрузка страницы лидов; не завязана на state page, чтобы смена страницы
  // не триггерила лишний вызов load(1) через эффекты
  const load = useCallback(async (p: number, s = pageSize) => {
    try {
      setLoading(true);
      const offset = (p - 1) * s;
      // Если массивы пустые — считаем, что выбрано «все», поэтому не передаём фильтр.
      const projectIdsFilter = projectIds.length ? projectIds : undefined;
      const sourcesFilter = sources.length ? sources : undefined;
      const collectionSourcesFilter = collectionSources.length ? collectionSources : undefined;
      const resp = await fetchLeads({
        projectIds: projectIdsFilter,
        sources: sourcesFilter,
        collectionSources: collectionSourcesFilter,
        fromDate,
        toDate,
        q: debouncedSearch.trim() || undefined,
        offset,
        limit: s,
      });
      setRows(resp.items);
      setTotal(resp.total);
    } catch (e: unknown) {
      console.error(e);
      setError(getErrorMessage(e, 'Не удалось загрузить лиды'));
    } finally {
      setLoading(false);
    }
  }, [pageSize, projectIds, sources, collectionSources, fromDate, toDate, debouncedSearch]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setPage(1);
      setDebouncedSearch(search);
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    load(1);
  }, [projectIds, sources, collectionSources, fromDate, toDate, debouncedSearch, load]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  function toggleChannel(code: string) {
    const all = sourcesList;
    const current = sources.length === 0 ? all : sources;
    const next = current.includes(code)
      ? current.filter((item) => item !== code)
      : [...current, code];
    const allSelected = all.length > 0 && all.every((item) => next.includes(item));
    setSources(allSelected ? [] : next);
    setPage(1);
  }

  const sourcesList = useMemo(() => {
    const preset = ['B1', 'B2', 'B3', 'B4'];
    const set = new Set<string>(preset);
    rows.forEach((r) => {
      if (r.source) set.add(r.source);
    });
    return Array.from(set).sort();
  }, [rows]);

  const handleExport = async (format: 'csv' | 'xlsx') => {
    await downloadLeadsExport({ projectIds, sources, collectionSources, fromDate, toDate, format, source: 'leads' });
  };

  return (
    <div className="table-card">
      <div className="table-toolbar leads-toolbar">
        <div className="filters leads-filters">
          {/* Универсальный выбор дат слева */}
          <DateRangeFilter
            from={fromDate}
            to={toDate}
            onChange={({ from, to }) => {
              setFromDate(from);
              setToDate(to);
            }}
          />

          <FilterDropdown
            label="Проекты"
            options={projects.map((p) => ({ value: String(p.id), label: formatProjectNameForDisplay(p.name) }))}
            selected={projectIds.map(String)}
            allLabel="Все проекты"
            onApply={(vals) => {
              setProjectIds(vals.length === projects.length ? [] : vals.map(Number));
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
          <div className="leads-channels">
            {getSourceCodeFilterOptions(sourcesList).map((option) => (
              <button
                type="button"
                key={option.value}
                className={`dashboard-source${sources.length === 0 || sources.includes(option.value) ? ' dashboard-source--active' : ''}`}
                aria-pressed={sources.length === 0 || sources.includes(option.value)}
                onClick={() => toggleChannel(option.value)}
              >
                {option.label}
              </button>
            ))}
          </div>

          <input
            type="search"
            placeholder="Поиск по телефону и источникам"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="actions leads-toolbar__actions">
          {loading ? <span className="sub">Загрузка…</span> : <span className="sub leads-total">Итого данных: {total}</span>}
          <ExportDropdown onExport={handleExport} />
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
          </tr>
        </thead>
        <tbody>
          {!loading && rows.length === 0 && (
            <tr>
              <td colSpan={7} className="muted" style={{ padding: 16, textAlign: 'center' }}>
                По выбранным фильтрам идентификаций не найдено.
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
                  {formatProjectNameForDisplay(
                    r.project_name ?? (r.project_id != null ? projectNameMap.get(r.project_id) : undefined) ?? '—',
                  )}
                  {r.project_id != null ? (
                    <span className="project-id-badge">
                      id{r.project_id}
                    </span>
                  ) : null}
                </div>
              </td>
              <td className="muted">{r.lk_id}</td>
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

export default LeadsTable;


