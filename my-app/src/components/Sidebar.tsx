import React from 'react';

function Sidebar() {
  return (
    <aside className="sidebar">
      <nav>
        <div className="nav-section">Основное</div>
        <ul>
          <li className="active">Проекты и каналы</li>
          <li>Идентификации</li>
          <li>Отчеты</li>
          <li>Интеграции</li>
        </ul>
        <div className="nav-section">Биллинг</div>
        <ul>
          <li>Баланс</li>
          <li>Черный список</li>
        </ul>
        <div className="nav-section">Обучение</div>
        <ul>
          <li>Обучение</li>
          <li>Онбординг</li>
        </ul>
      </nav>
    </aside>
  );
}

export default Sidebar;


