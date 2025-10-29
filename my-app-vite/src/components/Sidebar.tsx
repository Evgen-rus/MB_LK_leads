// Левое меню навигации по разделам личного кабинета
import { useEffect, useState } from 'react';

function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);

  // Автоколлапс на узких экранах
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 1200px)');
    const handle = () => setCollapsed(mq.matches);
    handle();
    mq.addEventListener('change', handle);
    return () => mq.removeEventListener('change', handle);
  }, []);

  return (
    <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
      <div className="sidebar__top">
        <button
          className="icon-btn"
          title={collapsed ? 'Развернуть меню' : 'Свернуть меню'}
          aria-label="Toggle sidebar"
          onClick={() => setCollapsed(v => !v)}
        >
          ☰
        </button>
        <div className="brand">
          <span className="brand__name">Mad Boss</span>
          <span className="brand__pill">leads</span>
        </div>
      </div>
      <div className="sidebar__info">Менеджер: Евгений Расюк</div>

      <nav>
        <div className="nav-section">Основное</div>
        <ul>
          <li className="active">
            <span className="nav-icon">📂</span>
            <span className="nav-label">Проекты</span>
          </li>
          <li>
            <span className="nav-icon">✅</span>
            <span className="nav-label">Идентификации</span>
          </li>
          <li>
            <span className="nav-icon">📊</span>
            <span className="nav-label">Отчеты</span>
          </li>
          <li>
            <span className="nav-icon">🔌</span>
            <span className="nav-label">Интеграции</span>
          </li>
        </ul>
        <div className="nav-section">Биллинг</div>
        <ul>
          <li>
            <span className="nav-icon">💳</span>
            <span className="nav-label">Баланс</span>
          </li>
          <li>
            <span className="nav-icon">⛔</span>
            <span className="nav-label">Черный список</span>
          </li>
        </ul>
        <div className="nav-section">Обучение</div>
        <ul>
          <li>
            <span className="nav-icon">🎓</span>
            <span className="nav-label">Обучение</span>
          </li>
          <li>
            <span className="nav-icon">📥</span>
            <span className="nav-label">Онбординг</span>
          </li>
        </ul>
      </nav>
    </aside>
  );
}

export default Sidebar;


