// Корневой лейаут приложения: левое меню и область контента с таблицей
import './App.css';
import Sidebar from './components/Sidebar';
import ProjectsTable from './components/ProjectsTable';
import LeadsTable from './components/LeadsTable';
import Integrations from './components/Integrations';
import Blacklist from './components/Blacklist';
import { useState, useEffect } from 'react';
import CreateProjectModal from './components/CreateProjectModal';
import EditProjectModal from './components/EditProjectModal';
import type { Project } from './types/project';
import { createProjects as apiCreate, fetchProjects as apiList, updateProject as apiUpdate, deleteProject as apiDelete, logout as apiLogout } from './api';
import Login from './components/Login';
import { isJwtValid } from './utils/jwt';

function App() {
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [rows, setRows] = useState<Project[]>([]);
  const [editing, setEditing] = useState<Project | null>(null);
  const [needLogin, setNeedLogin] = useState(false);
  const [authChecked, setAuthChecked] = useState(false);
  const [view, setView] = useState<'projects'|'leads'|'integrations'|'blacklist'>('projects');
  // Принудительно фиксируем светлую тему по умолчанию
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', 'light');
  }, []);

  // Предварительная проверка токена до любых запросов + установка URL
  useEffect(() => {
    try {
      const token = localStorage.getItem('access_token') || '';
      const valid = token ? isJwtValid(token) : false;
      if (!valid) {
        try { localStorage.removeItem('access_token'); } catch {}
        setNeedLogin(true);
        if (window.location.pathname !== '/login') {
          window.history.replaceState(null, '', '/login');
        }
      } else {
        setNeedLogin(false);
        if (window.location.pathname === '/login') {
          window.history.replaceState(null, '', '/');
        }
      }
    } finally {
      setAuthChecked(true);
    }
  }, []);

  // Загрузка данных после подтверждённой авторизации
  useEffect(() => {
    if (!authChecked || needLogin) return;
    (async () => {
      try {
        const data = await apiList({ limit: 10000 });
        setRows(data.items);
      } catch (e: any) {
        if (e?.status === 401) {
          setNeedLogin(true);
          try { localStorage.removeItem('access_token'); } catch {}
          if (window.location.pathname !== '/login') {
            window.history.replaceState(null, '', '/login');
          }
        } else {
          console.error(e);
        }
      }
    })();
  }, [authChecked, needLogin]);

  // Пауза до завершения первичной проверки, чтобы избежать «мигания»
  if (!authChecked) {
    return <div style={{display:'grid',placeItems:'center',height:'100vh'}}>Загрузка…</div>;
  }

  if (needLogin) {
    return <Login onSuccess={() => {
      // после успешного входа переключаем URL и загружаем данные
      window.history.replaceState(null, '', '/');
      setNeedLogin(false);
      (async () => {
        try {
          const data = await apiList({ limit: 10000 });
          setRows(data.items);
        } catch {}
      })();
    }} />;
  }

  return (
    <div className="layout">
      <div className="content">
        <Sidebar active={view} onNavigate={setView} />
        <main className="main">
          <div className="page-title" style={{display:'flex',alignItems:'center',justifyContent:'space-between', position:'relative'}}>
            <span>
              {view === 'projects' ? 'Проекты' : view === 'leads' ? 'Лиды' : view === 'integrations' ? 'Интеграции' : 'Черный список'}
            </span>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button className="btn btn--ghost" onClick={async ()=>{
              try { await apiLogout(); } catch {}
              setRows([]);
              setNeedLogin(true);
              if (window.location.pathname !== '/login') {
                window.history.replaceState(null, '', '/login');
              }
            }}>Выйти</button>
          </div>
          </div>
          {view === 'projects' ? (
          <ProjectsTable
            onCreate={() => setIsCreateOpen(true)}
            onDelete={(ids) => {
              if (!ids.length) return;
              (async () => {
                try {
                  await Promise.all(ids.map((id) => apiDelete(id)));
                  setRows((prev) => prev.filter((p) => !ids.includes(p.id)));
                  window.dispatchEvent(new CustomEvent('projects-refresh'));
                } catch (e) {
                  console.error(e);
                }
              })();
            }}
            onEdit={(row) => setEditing(row)}
          />
          ) : view === 'leads' ? (
            <LeadsTable projects={rows} />
          ) : view === 'integrations' ? (
            <Integrations />
          ) : (
            <Blacklist />
          )}
        </main>
      </div>
      {isCreateOpen && (
        <CreateProjectModal
          onClose={() => setIsCreateOpen(false)}
          onSubmit={(items) => {
            (async () => {
              try {
                const created = await apiCreate(items);
                setRows((prev) => [...created, ...prev]);
                window.dispatchEvent(new CustomEvent('projects-refresh'));
              } catch (e) {
                console.error(e);
              }
            })();
          }}
        />
      )}
      {editing && (
        <EditProjectModal
          project={editing}
          onClose={() => setEditing(null)}
          onSubmit={(u) => {
            (async () => {
              try {
                const updated = await apiUpdate(editing.id, u as any);
                setRows((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
                window.dispatchEvent(new CustomEvent('projects-refresh'));
              } catch (e) {
                console.error(e);
              }
            })();
          }}
        />
      )}
    </div>
  );
}

export default App;
