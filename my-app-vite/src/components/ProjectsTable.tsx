// Таблица проектов: фильтры, список, метрики и столбец «Настройки»
import { useMemo, useState } from 'react';
import type { Project } from '../types/project';

type ProjectsTableProps = {
  rows: Project[];
  onDelete?: (ids: number[]) => void;
};

function ProjectsTable({ rows, onDelete }: ProjectsTableProps) {
  const [search, setSearch] = useState<string>('');
  const [status, setStatus] = useState<'Все' | 'Активен' | 'На паузе'>('Все');
  const [selectedIds, setSelectedIds] = useState<number[]>([]);

  const filteredRows = useMemo<Project[]>(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((row) => {
      const matchesStatus = status === 'Все' ? true : row.status === status;
      const nameHit = row.name.toLowerCase().includes(q);
      const idHit = String(row.id).includes(q);
      const matchesQuery = q === '' ? true : (nameHit || idHit);
      return matchesStatus && matchesQuery;
    });
  }, [rows, search, status]);

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

  function handleDelete() {
    if (!onDelete || selectedIds.length === 0) return;
    if (!window.confirm(`Удалить выбранные проекты (${selectedIds.length})?`)) return;
    onDelete(selectedIds);
    setSelectedIds([]);
  }

  return (
    <div className="table-card">
      <div className="table-toolbar">
        <div className="filters">
          <input
            type="search"
            placeholder="Поиск по названию/ID"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <select value={status} onChange={(e) => setStatus(e.target.value as any)}>
            <option value="Все">Все статусы</option>
            <option value="Активен">Активен</option>
            <option value="На паузе">На паузе</option>
          </select>
          <select><option>Канал</option></select>
        </div>
        <div className="actions">
          <button className="btn" disabled={selectedIds.length === 0} onClick={handleDelete}>Удалить</button>
        </div>
      </div>
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
            <th>Тип</th>
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
                      : 'badge'
                  }
                >
                  {row.deliveryStatus}
                </span>
              </td>
              <td className="muted">{row.tag}</td>
              <td>
                <div className="name">{row.name}</div>
              </td>
              <td>
                <span className={row.status === 'Активен' ? 'badge badge--green' : 'badge badge--orange'}>
                  {row.status}
                </span>
              </td>
              <td>{row.type}</td>
              <td>{row.dataLimit}</td>
              <td>{row.numbersToday}</td>
              <td>{row.numbersTotal}</td>
              <td className="muted">{row.daysReceived}</td>
              <td>{row.sourcesCount}</td>
              <td className="muted">{row.createdAt}</td>
              <td>
                <button className="icon-btn" title="Настройки">⚙️</button>
                <button
                  className="icon-btn"
                  title="Удалить"
                  onClick={() => {
                    if (!onDelete) return;
                    if (!window.confirm(`Удалить проект ${row.id}?`)) return;
                    onDelete([row.id]);
                    setSelectedIds((prev) => prev.filter((id) => id !== row.id));
                  }}
                >
                  🗑️
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="table-footer">
        Показано {filteredRows.length} из {rows.length}
        <div className="spacer" />
        <div>
          <button className="btn btn--ghost">1</button>
          <select defaultValue={50}>
            <option>10</option>
            <option>25</option>
            <option>50</option>
          </select>
        </div>
      </div>
    </div>
  );
}

export default ProjectsTable;


