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

      <nav>
        <div className="nav-section">Основное</div>
        <ul>
          <li className="active">
            <span className="nav-icon" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="3" width="7" height="7" rx="1" />
                <rect x="14" y="3" width="7" height="7" rx="1" />
                <rect x="3" y="14" width="7" height="7" rx="1" />
                <rect x="14" y="14" width="7" height="7" rx="1" />
              </svg>
            </span>
            <span className="nav-label">Проекты</span>
          </li>
          <li>
            <span className="nav-icon" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="9" />
                <path d="M8 12l2.5 2.5L16 9" />
              </svg>
            </span>
            <span className="nav-label">Идентификации</span>
          </li>
          <li>
            <span className="nav-icon" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="4" y="10" width="3" height="8" />
                <rect x="10.5" y="6" width="3" height="12" />
                <rect x="17" y="13" width="3" height="5" />
              </svg>
            </span>
            <span className="nav-label">Отчеты</span>
          </li>
          <li>
            <span className="nav-icon" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M7 7l10 10" />
                <path d="M7 17a4 4 0 0 1 0-5.657L8.343 10a4 4 0 0 1 5.657 0" />
                <path d="M17 7a4 4 0 0 1 0 5.657L15.657 14a4 4 0 0 1-5.657 0" />
              </svg>
            </span>
            <span className="nav-label">Интеграции</span>
          </li>
          <li>
            <span className="nav-icon" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="9" />
                <line x1="7" y1="17" x2="17" y2="7" />
              </svg>
            </span>
            <span className="nav-label">Черный список</span>
          </li>
        </ul>
        <div className="nav-section">Биллинг</div>
        <ul>
          <li>
            <span className="nav-icon" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="6" width="18" height="12" rx="2" />
                <line x1="3" y1="10" x2="21" y2="10" />
              </svg>
            </span>
            <span className="nav-label">Баланс</span>
          </li>
        </ul>
        <div className="nav-section">Обучение</div>
        <ul>
          <li>
            <span className="nav-icon" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 19V7a2 2 0 0 1 2-2h6" />
                <path d="M8 5v14" />
                <path d="M12 5h6a2 2 0 0 1 2 2v12" />
              </svg>
            </span>
            <span className="nav-label">Обучение</span>
          </li>
          <li>
            <span className="nav-icon" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 12h16l-3 6H7l-3-6Z" />
                <path d="M12 3v9" />
                <path d="M9 6l3-3 3 3" />
              </svg>
            </span>
            <span className="nav-label">Онбординг</span>
          </li>
        </ul>
      </nav>
    </aside>
  );
}

export default Sidebar;


