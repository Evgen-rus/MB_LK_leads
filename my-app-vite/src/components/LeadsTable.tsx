// Таблица лидов с фильтрами по проекту и дате
import { useEffect, useState } from 'react';
import type { Project } from '../types/project';
import { fetchLeads, type Lead } from '../api';

type Props = {
  projects: Project[];
};

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

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        const data = await fetchLeads({ projectIds: allProjects ? [] : projectIds, fromDate, toDate });
        setRows(data);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    })();
  }, [allProjects, projectIds, fromDate, toDate]);

  return (
    <div className="table-card">
      <div className="table-toolbar">
        <div className="filters">
          <label style={{display:'inline-flex',alignItems:'center',gap:6, marginRight:8}}>
            <input type="checkbox" checked={allProjects} onChange={(e)=> setAllProjects(e.target.checked)} />
            Все проекты
          </label>
          <select multiple size={Math.min(6, Math.max(3, projects.length))} disabled={allProjects}
                  value={projectIds.map(String)}
                  onChange={(e)=> {
                    const opts = Array.from(e.currentTarget.selectedOptions).map(o=> Number(o.value));
                    setProjectIds(opts);
                  }}>
            {projects.map(p => (
              <option key={p.id} value={p.id}>{p.id} — {p.name}</option>
            ))}
          </select>
          <input type="date" value={fromDate} onChange={(e)=> setFromDate(e.target.value)} />
          <input type="date" value={toDate} onChange={(e)=> setToDate(e.target.value)} />
        </div>
        <div className="actions">
          {loading ? <span className="sub">Загрузка…</span> : <span className="sub">Найдено: {rows.length}</span>}
        </div>
      </div>
      <div className="table-scroll">
      <table className="table">
        <thead>
          <tr>
            <th>ext_id</th>
            <th>Дата</th>
            <th>Телефон</th>
            <th>UTM_CAMPAIGN</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, idx) => (
            <tr key={r.ext_id} className={idx % 2 === 0 ? 'row-alt' : ''}>
              <td className="muted">{r.ext_id}</td>
              <td>{r.created_at}</td>
              <td>{r.phone}</td>
              <td className="muted">{r.utm_campaign ?? ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      <div className="table-footer">
        Показано {rows.length}
      </div>
    </div>
  );
}

export default LeadsTable;


