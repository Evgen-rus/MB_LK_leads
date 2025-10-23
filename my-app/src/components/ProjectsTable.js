import React from 'react';

const mockRows = [
  { id: 198734, name: 'lr122_ladacenter', type: 'С кодом', globalLimit: 0, dayLimit: 300, status: 'Активен', ident: 0 },
  { id: 157203, name: 'lr104 клиника act (тест)', type: 'С кодом', globalLimit: 78, dayLimit: 30, status: 'Активен', ident: 0 },
  { id: 152352, name: 'lr104 клиника act', type: 'С кодом', globalLimit: 0, dayLimit: 300, status: 'Активен', ident: 0 },
  { id: 71422, name: '50. winnstrategy', type: 'С кодом', globalLimit: 0, dayLimit: 100, status: 'На паузе', ident: 0 },
  { id: 18487, name: '47. лиеднборюо', type: 'С кодом', globalLimit: 10000, dayLimit: 50, status: 'На паузе', ident: 0 },
  { id: 6902, name: '43. м-спорт', type: 'С кодом', globalLimit: 0, dayLimit: 50, status: 'На паузе', ident: 0 },
];

function ProjectsTable() {
  return (
    <div className="table-card">
      <div className="table-toolbar">
        <div className="filters">
          <select><option>Все типы</option></select>
          <select><option>Все статусы</option></select>
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
          {mockRows.map((row, index) => (
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
        Показано {mockRows.length} из {mockRows.length}
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


