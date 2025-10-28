import React, { useMemo, useState } from 'react';
import { projects } from '../data/projects';
import { Project } from '../types/project';

function ProjectsTable() {
  const [search, setSearch] = useState<string>('');
  const [status, setStatus] = useState<'Все' | 'Активен' | 'На паузе'>('Все');

  const filteredRows = useMemo<Project[]>(() => {
    const q = search.trim().toLowerCase();
    return projects.filter((row) => {
      const matchesStatus = status === 'Все' ? true : row.status === status;
      const nameHit = row.name.toLowerCase().includes(q);
      const idHit = String(row.id).includes(q);
      const matchesQuery = q === '' ? true : (nameHit || idHit);
      return matchesStatus && matchesQuery;
    });
  }, [search, status]);

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
          <button className="btn">Удалить</button>
        </div>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th style={{width: 32}}><input type="checkbox" /></th>
            <th>Название</th>
            <th>Тип канала</th>
            <th>Глобальный лимит</th>
            <th>Дневной лимит</th>
            <th>Статус</th>
            <th>Идентификация</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {filteredRows.map((row, index) => (
            <tr key={row.id} className={index % 2 === 0 ? 'row-alt' : ''}>
              <td><input type="checkbox" /></td>
              <td>
                <div className="name">{row.name}</div>
                <div className="sub">{row.id}</div>
              </td>
              <td>{row.type}</td>
              <td className="muted">{row.globalLimit}</td>
              <td>{row.dayLimit}</td>
              <td>
                <span className={row.status === 'Активен' ? 'badge badge--green' : 'badge badge--orange'}>
                  {row.status}
                </span>
              </td>
              <td className="muted">{row.ident}</td>
              <td>
                <button className="icon-btn" title="Удалить">🗑️</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="table-footer">
        Показано {filteredRows.length} из {projects.length}
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


