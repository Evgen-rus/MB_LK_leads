// Таблица лидов с фильтрами по проекту и дате
import { useEffect, useState } from 'react';
import type { Project } from '../types/project';
import { fetchLeads, buildLeadsExportUrl, type Lead } from '../api';
import ExportDropdown from './ExportDropdown';
import DateRangeFilter from './DateRangeFilter';

type Props = {
  projects: Project[];
};

// Формат для value инпута даты (YYYY-MM-DD)
function formatDateInput(d: Date) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function LeadsTable({ projects }: Props) {
  const [allProjects, setAllProjects] = useState<boolean>(true);
  const [projectIds, setProjectIds] = useState<number[]>([]);
  const [fromDate, setFromDate] = useState<string>(formatDateInput(new Date()));
  const [toDate, setToDate] = useState<string>(formatDateInput(new Date()));
  const [rows, setRows] = useState<Lead[]>([]);
  const [loading, setLoading] = useState(false);
  const [showProjectFilter, setShowProjectFilter] = useState<boolean>(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [total, setTotal] = useState(0);

  async function load(p = page, s = pageSize) {
    try {
      setLoading(true);
      const offset = (p - 1) * s;
      const resp = await fetchLeads({ projectIds: allProjects ? [] : projectIds, fromDate, toDate, offset, limit: s });
      setRows(resp.items);
      setTotal(resp.total);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(1);
  }, [allProjects, projectIds, fromDate, toDate]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const handleExport = (format: 'csv' | 'xlsx') => {
    const url = buildLeadsExportUrl({ projectIds: allProjects ? undefined : projectIds, fromDate, toDate, format });
    window.open(url, '_blank');
  };

  return (
    <div className="table-card">
      <div className="table-toolbar">
        <div className="filters" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {/* Универсальный выбор дат слева */}
          <DateRangeFilter
            from={fromDate}
            to={toDate}
            onChange={({ from, to }) => {
              setFromDate(from);
              setToDate(to);
            }}
          />

          {/* Фильтр по проектам остаётся как был */}
          <button className="btn" onClick={() => setShowProjectFilter((v) => !v)}>
            Проекты: {allProjects ? 'Все' : projectIds.length || 0} {showProjectFilter ? '▲' : '▼'}
          </button>
          {showProjectFilter && (
            <div className="project-filter-panel">
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <input
                  type="checkbox"
                  checked={allProjects}
                  onChange={(e) => setAllProjects(e.target.checked)}
                />
                Все проекты
              </label>
              <select
                multiple
                size={Math.min(6, Math.max(3, projects.length))}
                disabled={allProjects}
                value={projectIds.map(String)}
                onChange={(e) => {
                  const opts = Array.from(e.currentTarget.selectedOptions).map((o) =>
                    Number(o.value),
                  );
                  setProjectIds(opts);
                }}
              >
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.id} — {p.name}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
        <div className="actions" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {loading ? <span className="sub">Загрузка…</span> : <span className="sub">Найдено: {rows.length}</span>}
          <ExportDropdown onExport={handleExport} />
        </div>
      </div>
      <div className="table-scroll">
      <table className="table">
        <thead>
          <tr>
            <th>ext_id</th>
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

export default LeadsTable;


