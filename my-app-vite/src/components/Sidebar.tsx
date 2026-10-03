// Левое меню навигации по разделам личного кабинета
import { useEffect, useState } from 'react';

export type ViewType =
  | 'admin-dashboard'
  | 'client-dashboard'
  | 'agents'
  | 'projects'
  | 'leads'
  | 'reports'
  | 'analytics'
  | 'activity'
  | 'integrations'
  | 'support'
  | 'blacklist'
  | 'admin-clients'
  | 'admin-provider-import'
  | 'balance'
  | 'education'
  | 'onboarding';

type SidebarProps = {
  active: ViewType;
  onNavigate: (v: ViewType) => void;
  role?: 'admin' | 'agent' | 'client';
};

function Sidebar({ active, onNavigate, role = 'client' }: SidebarProps) {
  const [collapsed, setCollapsed] = useState(false);
  const isAdmin = role === 'admin';
  const isAgent = role === 'agent';

  // Автоколлапс на узких экранах
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 1200px)');
    const handle = () => setCollapsed(mq.matches);
    handle();
    mq.addEventListener('change', handle);
    return () => mq.removeEventListener('change', handle);
  }, []);

  function navigate(view: ViewType) {
    onNavigate(view);
    if (window.matchMedia('(max-width: 768px)').matches) {
      setCollapsed(true);
    }
  }

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
        {isAdmin && (
          <>
            <div className="nav-section">Администратор</div>
            <ul>
              <li className={active === 'admin-dashboard' ? 'active' : ''} onClick={() => navigate('admin-dashboard')}>
                <span className="nav-icon" aria-hidden>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="3" y="3" width="7" height="7" rx="1" />
                    <rect x="14" y="3" width="7" height="7" rx="1" />
                    <rect x="3" y="14" width="7" height="7" rx="1" />
                    <path d="M14 18h7" />
                    <path d="M17.5 14.5v7" />
                  </svg>
                </span>
                <span className="nav-label">Дашборд</span>
              </li>
              <li className={active === 'admin-clients' ? 'active' : ''} onClick={() => navigate('admin-clients')}>
                <span className="nav-icon" aria-hidden>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                    <circle cx="9" cy="7" r="4" />
                    <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
                    <path d="M16 3.13a4 4 0 0 1 0 7.75" />
                  </svg>
                </span>
                <span className="nav-label">Клиенты</span>
              </li>
              <li className={active === 'agents' ? 'active' : ''} onClick={() => navigate('agents')}>
                <span className="nav-icon" aria-hidden>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 12a5 5 0 1 0-5-5 5 5 0 0 0 5 5Z" />
                    <path d="M4 21a8 8 0 0 1 16 0" />
                    <path d="M19 8h4" />
                    <path d="M21 6v4" />
                  </svg>
                </span>
                <span className="nav-label">Агенты</span>
              </li>
              <li className={active === 'admin-provider-import' ? 'active' : ''} onClick={() => navigate('admin-provider-import')}>
                <span className="nav-icon" aria-hidden>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 3v12" />
                    <path d="m7 10 5 5 5-5" />
                    <path d="M5 21h14" />
                  </svg>
                </span>
                <span className="nav-label">Импорт лидов</span>
              </li>
            </ul>
          </>
        )}
        {isAgent && (
          <>
            <div className="nav-section">Управление</div>
            <ul>
              <li className={active === 'admin-clients' ? 'active' : ''} onClick={() => navigate('admin-clients')}>
                <span className="nav-icon" aria-hidden>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                    <circle cx="9" cy="7" r="4" />
                    <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
                    <path d="M16 3.13a4 4 0 0 1 0 7.75" />
                  </svg>
                </span>
                <span className="nav-label">Клиенты</span>
              </li>
            </ul>
          </>
        )}
        <div className="nav-section">Основное</div>
        <ul>
          {isAdmin && (
            <li className={active === 'analytics' ? 'active' : ''}>
              <button type="button" onClick={() => navigate('analytics')} style={{ display: 'flex', alignItems: 'center', gap: 10, width: '100%', background: 'none', border: 0, color: 'inherit', font: 'inherit', textAlign: 'left', padding: 0, cursor: 'pointer' }}>
                <span className="nav-icon" aria-hidden>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M4 20V4M4 20h16M8 15l4-6 4 3 4-7" />
                  </svg>
                </span>
                <span className="nav-label">Аналитика</span>
              </button>
            </li>
          )}
          {!isAdmin && !isAgent && (
            <li className={active === 'client-dashboard' ? 'active' : ''} onClick={() => navigate('client-dashboard')}>
              <span className="nav-icon" aria-hidden>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="3" width="7" height="7" rx="1" />
                  <rect x="14" y="3" width="7" height="7" rx="1" />
                  <rect x="3" y="14" width="7" height="7" rx="1" />
                  <path d="M14 18h7" />
                  <path d="M17.5 14.5v7" />
                </svg>
              </span>
              <span className="nav-label">Дашборд</span>
            </li>
          )}
          <li className={active === 'projects' ? 'active' : ''} onClick={() => navigate('projects')}>
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
          <li className={active === 'leads' ? 'active' : ''} onClick={() => navigate('leads')}>
            <span className="nav-icon" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="9" />
                <path d="M8 12l2.5 2.5L16 9" />
              </svg>
            </span>
            <span className="nav-label">Идентификации</span>
          </li>
          <li className={active === 'reports' ? 'active' : ''} onClick={() => navigate('reports')}>
            <span className="nav-icon" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="4" y="10" width="3" height="8" />
                <rect x="10.5" y="6" width="3" height="12" />
                <rect x="17" y="13" width="3" height="5" />
              </svg>
            </span>
            <span className="nav-label">Отчеты</span>
          </li>
          {(isAdmin || isAgent) && (
            <li className={active === 'activity' ? 'active' : ''} onClick={() => navigate('activity')}>
              <span className="nav-icon" aria-hidden>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3 12a9 9 0 1 0 3-6.7" />
                  <path d="M3 4v5h5" />
                  <path d="M12 7v5l3 2" />
                </svg>
              </span>
              <span className="nav-label">История изменений</span>
            </li>
          )}
          {!isAgent && (
            <li className={active === 'balance' ? 'active' : ''} onClick={() => navigate('balance')}>
              <span className="nav-icon" aria-hidden>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="6" width="18" height="12" rx="2" />
                  <line x1="3" y1="10" x2="21" y2="10" />
                </svg>
              </span>
              <span className="nav-label">Баланс</span>
            </li>
          )}
          <li className={active === 'blacklist' ? 'active' : ''} onClick={() => navigate('blacklist')}>
            <span className="nav-icon" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="9" />
                <line x1="7" y1="17" x2="17" y2="7" />
              </svg>
            </span>
            <span className="nav-label">Черный список</span>
          </li>
        </ul>
        <div className="nav-section">Помощь</div>
        <ul>
          <li className={active === 'support' ? 'active' : ''} onClick={() => navigate('support')}>
            <span className="nav-icon" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 6.5C4 5.12 5.12 4 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7A2.5 2.5 0 0 1 17.5 16H10l-3.5 3.5V16H6.5A2.5 2.5 0 0 1 4 13.5v-7Z" />
                <circle cx="9" cy="9.5" r="0.75" />
                <circle cx="12.5" cy="9.5" r="0.75" />
                <circle cx="16" cy="9.5" r="0.75" />
              </svg>
            </span>
            <span className="nav-label">Техподдержка</span>
          </li>
          <li className={active === 'integrations' ? 'active' : ''} onClick={() => navigate('integrations')}>
            <span className="nav-icon" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M7 7l10 10" />
                <path d="M7 17a4 4 0 0 1 0-5.657L8.343 10a4 4 0 0 1 5.657 0" />
                <path d="M17 7a4 4 0 0 1 0 5.657L15.657 14a4 4 0 0 1-5.657 0" />
              </svg>
            </span>
            <span className="nav-label">Интеграции</span>
          </li>
          <li className={active === 'education' ? 'active' : ''} onClick={() => navigate('education')}>
            <span className="nav-icon" aria-hidden>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 19V7a2 2 0 0 1 2-2h6" />
                <path d="M8 5v14" />
                <path d="M12 5h6a2 2 0 0 1 2 2v12" />
              </svg>
            </span>
            <span className="nav-label">Обучение</span>
          </li>
          <li className={active === 'onboarding' ? 'active' : ''} onClick={() => navigate('onboarding')}>
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


