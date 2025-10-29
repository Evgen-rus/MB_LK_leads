// Таблица проектов: фильтры, список, метрики и столбец «Настройки»
import { useMemo, useState } from 'react';
import type { Project, DeliveryStatus, CollectionSource } from '../types/project';

type ProjectsTableProps = {
  rows: Project[];
  onDelete?: (ids: number[]) => void;
  onEdit?: (row: Project) => void;
  onCreate?: () => void;
};

function ProjectsTable({ rows, onDelete, onEdit, onCreate }: ProjectsTableProps) {
  const [search, setSearch] = useState<string>('');
  const [status, setStatus] = useState<'Все' | 'Активен' | 'На паузе'>('Все');
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [deliveryStatus, setDeliveryStatus] = useState<'Все' | DeliveryStatus>('Все');
  const [typeFilter, setTypeFilter] = useState<'Все' | CollectionSource>('Все');

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


