// Таблица проектов: фильтры, список, метрики и столбец «Настройки»
import { useEffect, useMemo, useState } from 'react';
import type { Project, DeliveryStatus, CollectionSource } from '../types/project';
import { fetchProjects, deleteProject as apiDelete } from '../api';

type ProjectsTableProps = {
  onDelete?: (ids: number[]) => void;
  onEdit?: (row: Project) => void;
  onCreate?: () => void;
};

function ProjectsTable({ onDelete, onEdit, onCreate }: ProjectsTableProps) {
  const [rows, setRows] = useState<Project[]>([]);
  const [search, setSearch] = useState<string>('');
  const [status, setStatus] = useState<'Все' | 'Активен' | 'На паузе'>('Все');
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [deliveryStatus, setDeliveryStatus] = useState<'Все' | DeliveryStatus>('Все');
  const [typeFilter, setTypeFilter] = useState<'Все' | CollectionSource>('Все');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [total, setTotal] = useState(0);
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  async function load(p = page, s = pageSize, q = search) {
    const offset = (p - 1) * s;
    const resp = await fetchProjects({ offset, limit: s, q: q.trim() || undefined });
    setRows(resp.items);
    setTotal(resp.total);
  }

  useEffect(() => { load(1); }, []);
  useEffect(() => {
    // Внешний сигнал обновить список
    const h = () => load(page);
    window.addEventListener('projects-refresh', h as any);
    return () => window.removeEventListener('projects-refresh', h as any);
  }, [page, pageSize, search]);

  const filteredRows = useMemo<Project[]>(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((row) => {
      const matchesStatus = status === 'Все' ? true : row.status === status;
      const matchesDelivery = deliveryStatus === 'Все' ? true : row.deliveryStatus === deliveryStatus;
      const matchesType = typeFilter === 'Все' ? true : row.collectionSource === typeFilter;
      const nameHit = row.name.toLowerCase().includes(q);
      const idHit = String(row.id).includes(q);
      const matchesQuery = q === '' ? true : (nameHit || idHit);
      return matchesStatus && matchesDelivery && matchesType && matchesQuery;
    });
  }, [rows, search, status, deliveryStatus, typeFilter]);

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

  // Удаление из тулбара не используется — по просьбе отключено

  return (
    <div className="table-card">
      <div className="table-toolbar">
        <div className="filters">
          <input
            type="search"
            placeholder="Поиск по названию/ID"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e)=> { if (e.key==='Enter') { setPage(1); load(1, pageSize, (e.target as HTMLInputElement).value); }}}
          />
          <select value={deliveryStatus} onChange={(e) => setDeliveryStatus(e.target.value as 'Все' | DeliveryStatus)}>
            <option value="Все">Все статусы отгрузки</option>
            <option value="Активна">Активна</option>
            <option value="На модерации">На модерации</option>
            <option value="Отключена">Отключена</option>
          </select>
          <select value={status} onChange={(e) => setStatus(e.target.value as any)}>
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
        </div>
        <div className="actions">
          <button className="btn btn--primary" onClick={onCreate}>+ Добавить проект</button>
        </div>
      </div>
      <div className="table-scroll">
      <table className="table">
        <thead>
          <tr>
            <th style={{width: 32}}>
              <input
                type="checkbox"
                checked={allOnPageSelected}
                onChange={toggleAllOnPage}
              />
            </th>
            <th>ID</th>
            <th>Статус отгрузки</th>
            <th>Тег</th>
            <th>Название</th>
            <th>Статус проекта</th>
            <th>Источник сбора</th>
            <th>Лимит</th>
            <th>Номеров получено сегодня</th>
            <th>Номеров получено всего</th>
            <th>Дни получения номеров</th>
            <th>Доменов/номеров</th>
            <th>Дата создания</th>
            <th>Настройки</th>
          </tr>
        </thead>
        <tbody>
          {filteredRows.map((row, index) => (
            <tr key={row.id} className={index % 2 === 0 ? 'row-alt' : ''}>
              <td>
                <input
                  type="checkbox"
                  checked={selectedIds.includes(row.id)}
                  onChange={() => toggleRow(row.id)}
                />
              </td>
              <td className="muted">{row.id}</td>
              <td>
                <span
                  className={
                    row.deliveryStatus === 'Активна'
                      ? 'badge badge--green'
                      : row.deliveryStatus === 'На модерации'
                      ? 'badge badge--orange'
                      : 'badge badge--gray'
                  }
                  style={{ whiteSpace: 'nowrap' }}
                >
                  {row.deliveryStatus}
                </span>
              </td>
              <td className="muted">{row.tag}</td>
              <td>
                <div className="name">{row.name}</div>
              </td>
              <td>
                <span className={row.status === 'Активен' ? 'badge badge--green' : 'badge badge--orange'}
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
              <td className="muted">{row.createdAt}</td>
              <td>
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
                        setSelectedIds((prev) => prev.filter((id) => id !== row.id));
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


