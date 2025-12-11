// Таблица проектов: фильтры, список, метрики и столбец «Настройки»
import { useEffect, useMemo, useState } from 'react';
import type { Project, CollectionSource } from '../types/project';
import { fetchProjects, deleteProject as apiDelete, updateProject as apiUpdateProject, type ProjectUpdatePayload, type Day } from '../api';
import DateRangeFilter from './DateRangeFilter';

type ProjectsTableProps = {
  onDelete?: (ids: number[]) => void;
  onEdit?: (row: Project) => void;
  onCreate?: () => void;
  onHistory?: (row: Project) => void;
};

function formatDateInput(d: Date) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function ProjectsTable({ onDelete, onEdit, onCreate, onHistory }: ProjectsTableProps) {
  const [rows, setRows] = useState<Project[]>([]);
  const [search, setSearch] = useState<string>('');
  const [fromDate, setFromDate] = useState<string>(formatDateInput(new Date()));
  const [toDate, setToDate] = useState<string>(formatDateInput(new Date()));
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [total, setTotal] = useState(0);
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  async function load(p = page, s = pageSize, q = search, from = fromDate, to = toDate) {
    const offset = (p - 1) * s;
    const resp = await fetchProjects({
      offset,
      limit: s,
      q: q.trim() || undefined,
      fromDate: from,
      toDate: to,
    });
    setRows(resp.items);
    setTotal(resp.total);
  }

  useEffect(() => { load(1); }, []);
  useEffect(() => {
    // Внешний сигнал обновить список
    const h = () => load(page);
    window.addEventListener('projects-refresh', h as any);
    return () => window.removeEventListener('projects-refresh', h as any);
  }, [page, pageSize, search, fromDate, toDate]);

  const filteredRows = useMemo<Project[]>(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((row) => {
      const nameHit = row.name.toLowerCase().includes(q);
      const idHit = String(row.id).includes(q);
      const matchesQuery = q === '' ? true : (nameHit || idHit);
      return matchesQuery;
    });
  }, [rows, search]);

  // Восстанавливаем payload для updateProject из текущего объекта Project.
  // Нужен полный набор полей, иначе бэкенд отвечает 422.
  function buildUpdatePayloadFromRow(row: Project, patch: Partial<ProjectUpdatePayload>): ProjectUpdatePayload {
    const map: Record<string, Day> = {
      'Пн.': 'Пн',
      'Вт.': 'Вт',
      'Ср.': 'Ср',
      'Чт.': 'Чт',
      'Пт.': 'Пт',
      'Сб.': 'Сб',
      'Вс.': 'Вс',
    };
    const parts = (row.daysReceived || '').split(/\s+/).filter(Boolean);
    const days: Day[] = [];
    parts.forEach((p) => {
      if (map[p]) days.push(map[p]);
    });
    const daysFinal: Day[] = days.length ? days : (['Вт', 'Ср', 'Чт', 'Пт', 'Сб'] as Day[]);

    return {
      name: row.name,
      tag: row.tag || row.name,
      status: row.status,
      dataLimit: row.dataLimit,
      regionMode: row.regionMode || 'include',
      regions: row.regions || [],
      sites: row.sites || undefined,
      phones: row.phones || undefined,
      smsSenderName: row.smsSenderName || undefined,
      days: daysFinal,
      ...patch,
    };
  }

  // Переключение статуса проекта (Активен <-> На паузе) для клиентского ЛК.
  // Это реальный PATCH на бэк; при ошибке статус визуально не меняется.
  async function handleToggleStatus(row: Project) {
    const nextStatus = row.status === 'Активен' ? 'На паузе' : 'Активен';
    try {
      const payload = buildUpdatePayloadFromRow(row, { status: nextStatus });
      const updated = await apiUpdateProject(row.id, payload);
      setRows((prev) => prev.map((p) => (p.id === row.id ? updated : p)));
      window.dispatchEvent(new CustomEvent('projects-refresh'));
    } catch (e) {
      console.error(e);
      alert('Не удалось изменить статус проекта');
    }
  }

  // Удаление из тулбара не используется — по просьбе отключено

  return (
    <div className="table-card">
      <div className="table-toolbar">
        <div className="filters" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {/* Период для пересчёта показателей проектов */}
          <DateRangeFilter
            from={fromDate}
            to={toDate}
            onChange={({ from, to }) => {
              setFromDate(from);
              setToDate(to);
              setPage(1);
              load(1, pageSize, search, from, to);
            }}
          />

          <input
            type="search"
            placeholder="Поиск по названию/ID"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e)=> { if (e.key==='Enter') { setPage(1); load(1, pageSize, (e.target as HTMLInputElement).value, fromDate, toDate); }}}
          />
        </div>
        <div className="actions">
          <button className="btn btn--primary" onClick={onCreate}>+ Добавить проект</button>
        </div>
      </div>
      <div className="table-scroll">
      <table className="table">
        <thead>
          <tr>
            <th>Название</th>
            <th>Источник</th>
            <th>Статус проекта</th>
            <th>Лимит</th>
            <th>Номеров за период</th>
            <th>Номеров получено всего</th>
            <th>Дни получения номеров</th>
            <th>Источник сбора</th>
            <th>Доменов/номеров</th>
            <th>Дата создания</th>
            <th>Настройки</th>
          </tr>
        </thead>
        <tbody>
          {filteredRows.map((row, index) => (
            <tr key={row.id} className={index % 2 === 0 ? 'row-alt' : ''}>
              <td>
                <div className="name">{row.name}</div>
                <div className="sub muted">ID: {row.id}</div>
              </td>
              <td>{row.dataSourceCode}</td>
              <td>
                <span
                  className={row.status === 'Активен' ? 'badge badge--green' : 'badge badge--orange'}
                  style={{ whiteSpace: 'nowrap', cursor: 'pointer' }}
                  title="Нажмите, чтобы переключить статус проекта"
                  onClick={() => handleToggleStatus(row)}
                >
                  {row.status}
                </span>
              </td>
              <td>{row.dataLimit}</td>
              <td>{row.numbersPeriod ?? row.numbersToday}</td>
              <td>{row.numbersTotal}</td>
              <td className="muted">{row.daysReceived}</td>
              <td>{row.collectionSource}</td>
              <td>{row.sourcesCount}</td>
              <td className="muted">{row.createdAt}</td>
              <td>
                <button
                  className="icon-btn"
                  title="История изменений"
                  onClick={() => onHistory?.(row)}
                  style={{ marginRight: 4 }}
                >
                  📜
                </button>
                <button className="icon-btn" title="Настройки" onClick={() => onEdit?.(row)}>⚙️</button>
                <button
                  className="icon-btn"
                  title="Удалить"
                  onClick={() => {
                    if (!window.confirm(`Удалить проект ${row.id}?`)) return;
                    (async () => {
                      try {
                        if (onDelete) {
                          onDelete([row.id]);
                        } else {
                          await apiDelete(row.id);
                        }
                        window.dispatchEvent(new CustomEvent('projects-refresh'));
                      } catch (e) {
                        console.error(e);
                      }
                    })();
                  }}
                >
                  🗑️
                </button>
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

export default ProjectsTable;


