// Корневой лейаут приложения: левое меню и область контента с таблицей
import './App.css';
import Sidebar, { type ViewType } from './components/Sidebar';
import ProjectsTable from './components/ProjectsTable';
import LeadsTable from './components/LeadsTable';
import AdminLeadsTable from './components/AdminLeadsTable';
import Integrations from './components/Integrations';
import Support from './components/Support';
import Blacklist from './components/Blacklist';
import AdminBlacklist from './components/AdminBlacklist';
import Reports from './components/Reports';
import AdminReports from './components/AdminReports';
import AdminClientsScreen from './components/AdminClientsScreen';
import AdminProviderLeadsImport from './components/AdminProviderLeadsImport';
import AdminProjectsScreen, {
  type AdminProjectsFocus,
} from './components/AdminProjectsScreen';
import AdminBalance from './components/AdminBalance';
import ClientBalance from './components/ClientBalance';
import ClientActivityHistory from './components/ClientActivityHistory';
import NotificationBell from './components/NotificationBell';
import { useState, useEffect } from 'react';
import CreateProjectModal from './components/CreateProjectModal';
import EditProjectModal from './components/EditProjectModal';
import ProjectHistoryModal from './components/ProjectHistoryModal';
import type { Project } from './types/project';
import {
  createProjects as apiCreate,
  fetchProjects as apiList,
  updateProject as apiUpdate,
  logout as apiLogout,
  fetchMe,
  fetchClientBalanceSummary,
  type ProjectUpdatePayload,
} from './api';
import Login from './components/Login';
import { isJwtValid, isAdminFromToken } from './utils/jwt';

const STORAGE_VIEW_KEY = 'last_view';

function App() {
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [rows, setRows] = useState<Project[]>([]);
  const [editing, setEditing] = useState<Project | null>(null);
  const [historyFor, setHistoryFor] = useState<Project | null>(null);
  const [needLogin, setNeedLogin] = useState(false);
  const [authChecked, setAuthChecked] = useState(false);
  const [view, setView] = useState<ViewType>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_VIEW_KEY);
      if (
        saved === 'projects' ||
        saved === 'leads' ||
        saved === 'reports' ||
        saved === 'activity' ||
        saved === 'integrations' ||
        saved === 'support' ||
        saved === 'blacklist' ||
        saved === 'admin-clients' ||
        saved === 'admin-provider-import' ||
        saved === 'balance' ||
        saved === 'education' ||
        saved === 'onboarding'
      ) {
        return saved as ViewType;
      }
    } catch {
      /* ignore */
    }
    return 'projects';
  });
  const [isAdmin, setIsAdmin] = useState(false);
  // Состояние только для админов: какой клиент выбран во вкладке «Проекты»
  const [adminProjectsClientId, setAdminProjectsClientId] = useState<number | null>(null);
  const [adminProjectsClientName, setAdminProjectsClientName] = useState<string | null>(null);
  const [adminProjectsFocus, setAdminProjectsFocus] = useState<AdminProjectsFocus>('projects');
  // Состояние только для админов: фильтр клиента в «Черном списке»
  const [adminBlacklistClientId, setAdminBlacklistClientId] = useState<number | null>(null);
  // Состояние для баланса: выбранный клиент и какая модалка открыть
  const [adminBalanceClientId, setAdminBalanceClientId] = useState<number | null>(null);
  const [adminBalanceModalType, setAdminBalanceModalType] = useState<'credit' | 'debit' | null>(null);
  // Предзаполнение фильтров идентификаций при переходе из «Проектов»
  const [leadsPrefill, setLeadsPrefill] = useState<{ projectId?: number; from?: string; to?: string } | null>(null);
  // Предзаполнение идентификаций для админа (клиент + проект)
  const [adminLeadsPrefill, setAdminLeadsPrefill] = useState<{ clientId?: number; projectId?: number; from?: string; to?: string } | null>(null);
  // Клиентский баланс для шапки
  const [clientBalance, setClientBalance] = useState<{ remaining: number; debt: boolean } | null>(null);
  const [clientName, setClientName] = useState<string | null>(null);
  const [projectsMutationLocked, setProjectsMutationLocked] = useState(false);
  const [projectsMutationLockReason, setProjectsMutationLockReason] = useState<string | null>(null);
  const [uniqueProjectNamesEnabled, setUniqueProjectNamesEnabled] = useState(false);
  // Принудительно фиксируем светлую тему по умолчанию
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', 'light');
  }, []);

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<string | null>).detail;
      if (detail && detail.trim()) {
        setToast(detail);
      }
    };
    window.addEventListener('app-toast', handler as EventListener);
    return () => window.removeEventListener('app-toast', handler as EventListener);
  }, []);

  // Предварительная проверка токена до любых запросов + установка URL
  useEffect(() => {
    const applyToken = () => {
      try {
        const token = localStorage.getItem('access_token') || '';
        const valid = token ? isJwtValid(token) : false;
        if (!valid) {
          try {
            localStorage.removeItem('access_token');
          } catch (err) {
            console.warn('Не удалось очистить токен', err);
          }
          setNeedLogin(true);
          setIsAdmin(false);
          if (window.location.pathname !== '/login') {
            window.history.replaceState(null, '', '/login');
          }
        } else {
          setNeedLogin(false);
          setIsAdmin(isAdminFromToken(token));
          if (window.location.pathname === '/login') {
            window.history.replaceState(null, '', '/');
          }
        }
      } finally {
        setAuthChecked(true);
      }
    };

    applyToken();

    // Обработчик смены токена (имперсонация из другой вкладки)
    const onStorage = (e: StorageEvent) => {
      if (e.key === 'access_token') {
        applyToken();
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  // Подтягиваем сохранённую вкладку после определения роли; если нет прав — откатываем.
  useEffect(() => {
    if (!authChecked || needLogin) return;
    if (!isAdmin && (view === 'admin-clients' || view === 'admin-provider-import')) {
      setView('projects');
      try {
        localStorage.setItem(STORAGE_VIEW_KEY, 'projects');
      } catch {
        /* ignore */
      }
      return;
    }
    if (isAdmin && view === 'activity') {
      setView('admin-clients');
      try {
        localStorage.setItem(STORAGE_VIEW_KEY, 'admin-clients');
      } catch {
        /* ignore */
      }
      return;
    }
    // если вдруг сохранённая вкладка невалидная, откатываем
    if (
      view !== 'projects' &&
      view !== 'leads' &&
      view !== 'reports' &&
      view !== 'activity' &&
      view !== 'integrations' &&
      view !== 'support' &&
      view !== 'blacklist' &&
      view !== 'admin-clients' &&
      view !== 'admin-provider-import' &&
      view !== 'balance' &&
      view !== 'education' &&
      view !== 'onboarding'
    ) {
      setView('projects');
      try {
        localStorage.setItem(STORAGE_VIEW_KEY, 'projects');
      } catch {
        /* ignore */
      }
    }
  }, [authChecked, needLogin, isAdmin, view]);

  // Загрузка данных после подтверждённой авторизации
  useEffect(() => {
    if (!authChecked || needLogin) return;
    (async () => {
      try {
        const data = await apiList({ limit: 10000 });
        setRows(data.items);
      } catch (e: unknown) {
        if (typeof e === 'object' && e !== null && 'status' in e && (e as { status?: number }).status === 401) {
          setNeedLogin(true);
          try {
            localStorage.removeItem('access_token');
          } catch (err) {
            console.warn('Не удалось очистить токен', err);
          }
          if (window.location.pathname !== '/login') {
            window.history.replaceState(null, '', '/login');
          }
        } else {
          console.error(e);
        }
      }
    })();
  }, [authChecked, needLogin]);

  // Подтягиваем баланс клиента для шапки (только для клиентской роли)
  useEffect(() => {
    if (!authChecked || needLogin || isAdmin) return;
    (async () => {
      try {
        const data = await fetchClientBalanceSummary();
        setClientBalance({ remaining: data.remaining, debt: data.debt });
      } catch (e) {
        console.error(e);
      }
    })();
  }, [authChecked, needLogin, isAdmin]);

  // Подтягиваем имя клиента для заголовка (только клиентская роль)
  useEffect(() => {
    if (!authChecked || needLogin || isAdmin) {
      setClientName(null);
      setProjectsMutationLocked(false);
      setProjectsMutationLockReason(null);
      setUniqueProjectNamesEnabled(false);
      return;
    }
    (async () => {
      try {
        const me = await fetchMe();
        setClientName(me.name || me.login || null);
        setProjectsMutationLocked(Boolean(me.projectsMutationLocked));
        setProjectsMutationLockReason(me.projectsMutationLockReason || null);
        setUniqueProjectNamesEnabled(Boolean(me.uniqueProjectNamesEnabled));
      } catch (e) {
        console.error(e);
        setClientName(null);
        setProjectsMutationLocked(false);
        setProjectsMutationLockReason(null);
        setUniqueProjectNamesEnabled(false);
      }
    })();
  }, [authChecked, needLogin, isAdmin]);

  // Пауза до завершения первичной проверки, чтобы избежать «мигания»
  if (!authChecked) {
    return <div style={{display:'grid',placeItems:'center',height:'100vh'}}>Загрузка…</div>;
  }

  if (needLogin) {
    return <Login onSuccess={() => {
      // после успешного входа переключаем URL и загружаем данные
      window.history.replaceState(null, '', '/');
      const token = localStorage.getItem('access_token') || '';
      const admin = isAdminFromToken(token);
      setIsAdmin(admin);

      // Требование: дефолтная вкладка выставляется ТОЛЬКО после ввода логина/пароля.
      // При обычном обновлении страницы остаёмся на last_view.
      const nextView: ViewType = admin ? 'admin-clients' : 'leads';
      setView(nextView);
      try {
        localStorage.setItem(STORAGE_VIEW_KEY, nextView);
      } catch {
        /* ignore */
      }
      setNeedLogin(false);
      (async () => {
        try {
          const data = await apiList({ limit: 10000 });
          setRows(data.items);
        } catch (err) {
          console.error(err);
        }
      })();
    }} />;
  }

  return (
    <div className="layout">
      <div className="content">
        <Sidebar
          active={view}
          onNavigate={(next) => {
            setView(next);
            try {
              localStorage.setItem(STORAGE_VIEW_KEY, next);
            } catch {
              /* ignore */
            }
            // При переключении вкладок не трогаем выбранного клиента,
            // чтобы можно было вернуться обратно в «Проекты» с тем же контекстом.
          }}
          isAdmin={isAdmin}
        />
        <main className="main">
          <div className="page-title page-title--main">
            <div className="page-title__left">
              <span className="page-title__title">
                {view === 'admin-clients'
                  ? 'Клиенты'
                  : view === 'admin-provider-import'
                  ? 'Импорт лидов'
                  : view === 'projects'
                  ? 'Проекты'
                  : view === 'leads'
                  ? 'Идентификации'
                  : view === 'reports'
                  ? 'Отчёты'
                  : view === 'activity'
                  ? 'История изменений'
                  : view === 'balance'
                  ? 'Баланс'
                  : view === 'integrations'
                  ? 'Интеграции'
                  : view === 'support'
                  ? 'Техподдержка'
                  : view === 'education'
                  ? 'Обучение'
                  : view === 'onboarding'
                  ? 'Онбординг'
                  : 'Черный список'}
              </span>
              {!isAdmin && clientName && (
                <span
                  className="sub"
                  style={{
                    color: '#555',
                    maxWidth: 240,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                  title={clientName}
                >
                  {clientName}
                </span>
              )}
            </div>
          <div className="page-title__right">
            {!isAdmin && clientBalance && (
              <div className="page-title__balance" style={{ color: clientBalance.debt ? '#d23' : '#111' }}>
                <div className="page-title__balance-value">
                  <span className="page-title__balance-label-full">Текущий остаток:</span>
                  <span className="page-title__balance-label-short">Остаток:</span>{' '}
                  {clientBalance.remaining}
                </div>
                {clientBalance.debt && <div className="sub" style={{ color: '#d23' }}>Долг</div>}
              </div>
            )}
            {!isAdmin && (
              <NotificationBell
                onOpenFullHistory={() => {
                  setView('activity');
                  try {
                    localStorage.setItem(STORAGE_VIEW_KEY, 'activity');
                  } catch {
                    /* ignore */
                  }
                }}
              />
            )}
            <button className="btn btn--ghost" onClick={async ()=>{
              try {
                await apiLogout();
              } catch (err) {
                console.error('Ошибка выхода', err);
              }
              setClientName(null);
              setRows([]);
              setUniqueProjectNamesEnabled(false);
              setNeedLogin(true);
              if (window.location.pathname !== '/login') {
                window.history.replaceState(null, '', '/login');
              }
            }}>Выйти</button>
          </div>
          </div>
          {view === 'admin-clients' && isAdmin ? (
            <AdminClientsScreen
              // Переход к проектам клиента из вкладки «Клиенты»
              onOpenClientProjects={(clientId, clientName) => {
                setAdminProjectsClientId(clientId);
                setAdminProjectsClientName(clientName);
                setAdminProjectsFocus('projects');
                setView('projects');
              }}
              // Быстрый переход к изменениям клиента во вкладке «Проекты»
              onOpenClientChanges={(clientId, clientName) => {
                setAdminProjectsClientId(clientId);
                setAdminProjectsClientName(clientName);
                setAdminProjectsFocus('changes');
                setView('projects');
              }}
              // Быстрый переход к событиям ЧС клиента во вкладке «Проекты»
              onOpenClientBlacklistChanges={(clientId, clientName) => {
                setAdminProjectsClientId(clientId);
                setAdminProjectsClientName(clientName);
                setAdminProjectsFocus('blacklist-changes');
                setView('projects');
              }}
              onOpenClientBalance={(clientId, _clientName, action) => {
                setAdminBalanceClientId(clientId);
                setAdminBalanceModalType(action);
                setView('balance');
              }}
            />
          ) : view === 'admin-provider-import' && isAdmin ? (
            <AdminProviderLeadsImport />
          ) : view === 'projects' ? (
            isAdmin ? (
              <AdminProjectsScreen
                initialClientId={adminProjectsClientId ?? undefined}
                initialClientName={adminProjectsClientName ?? undefined}
                initialFocus={adminProjectsFocus}
                onOpenClientBlacklist={({ clientId }) => {
                  setAdminBlacklistClientId(clientId);
                  setView('blacklist');
                  try {
                    localStorage.setItem(STORAGE_VIEW_KEY, 'blacklist');
                  } catch {
                    /* ignore */
                  }
                }}
                onOpenLeads={({ clientId, projectId, fromDate, toDate }) => {
                  setAdminLeadsPrefill({ clientId, projectId, from: fromDate, to: toDate });
                  setView('leads');
                  try {
                    localStorage.setItem(STORAGE_VIEW_KEY, 'leads');
                  } catch {
                    /* ignore */
                  }
                }}
              />
            ) : (
              <ProjectsTable
                projectsMutationLocked={projectsMutationLocked}
                projectsMutationLockMessage={
                  projectsMutationLockReason || 'Изменение проектов временно заблокировано администратором.'
                }
                onCreate={() => {
                  if (projectsMutationLocked) {
                    window.dispatchEvent(
                      new CustomEvent('app-toast', {
                        detail: projectsMutationLockReason || 'Изменение проектов временно заблокировано администратором.',
                      }),
                    );
                    return;
                  }
                  setIsCreateOpen(true);
                }}
                onEdit={(row) => {
                  if (projectsMutationLocked) {
                    window.dispatchEvent(
                      new CustomEvent('app-toast', {
                        detail: projectsMutationLockReason || 'Изменение проектов временно заблокировано администратором.',
                      }),
                    );
                    return;
                  }
                  setEditing(row);
                }}
                onHistory={(row) => setHistoryFor(row)}
                onOpenLeads={({ projectId, fromDate, toDate }) => {
                  setLeadsPrefill({ projectId, from: fromDate, to: toDate });
                  setView('leads');
                  try {
                    localStorage.setItem(STORAGE_VIEW_KEY, 'leads');
                  } catch {
                    /* ignore */
                  }
                }}
              />
            )
          ) : view === 'leads' ? (
            isAdmin ? <AdminLeadsTable initialFilter={adminLeadsPrefill ?? undefined} /> : <LeadsTable projects={rows} initialFilter={leadsPrefill ?? undefined} />
          ) : view === 'reports' ? (
            isAdmin ? <AdminReports /> : <Reports />
        ) : view === 'activity' ? (
          isAdmin ? (
            <div className="table-card" style={{ padding: 16 }}>
              Раздел истории изменений доступен только в клиентском ЛК.
            </div>
          ) : (
            <ClientActivityHistory />
          )
        ) : view === 'balance' ? (
          isAdmin ? (
            <AdminBalance
              initialClientId={adminBalanceClientId ?? undefined}
              initialModalType={adminBalanceModalType ?? undefined}
            />
          ) : (
            <ClientBalance />
          )
        ) : view === 'education' ? (
          <div className="table-card" style={{ padding: 16 }}>
            Раздел «Обучение» в разработке.
          </div>
        ) : view === 'onboarding' ? (
          <div className="table-card" style={{ padding: 16 }}>
            Раздел «Онбординг» в разработке.
          </div>
          ) : view === 'integrations' ? (
            <Integrations />
          ) : view === 'support' ? (
            <Support />
          ) : (
            isAdmin ? <AdminBlacklist initialUserId={adminBlacklistClientId ?? undefined} /> : <Blacklist />
          )}
        </main>
      </div>
      {isCreateOpen && (
        <CreateProjectModal
          uniqueProjectNamesEnabled={uniqueProjectNamesEnabled}
          onClose={() => setIsCreateOpen(false)}
          onSubmit={async (items) => {
            try {
              const result = await apiCreate(items);
              setRows((prev) => [...result.items, ...prev]);
              window.dispatchEvent(new CustomEvent('projects-refresh'));
              if (result.warning) {
                setToast(result.warning);
              }
              return null;
            } catch (e) {
              console.error(e);
              if (e instanceof Error && e.message) {
                return e.message;
              }
              return 'Не удалось создать проект. Проверьте введенные данные и попробуйте еще раз.';
            }
          }}
        />
      )}
      {editing && (
        <EditProjectModal
          project={editing}
          onClose={() => setEditing(null)}
          onSubmit={async (u: ProjectUpdatePayload) => {
            const result = await apiUpdate(editing.id, u);
            setRows((prev) => prev.map((p) => (p.id === result.project.id ? result.project : p)));
            window.dispatchEvent(new CustomEvent('projects-refresh'));
            if (result.warning) {
              setToast(result.warning);
            }
          }}
        />
      )}
      {historyFor && (
        <ProjectHistoryModal
          projectId={historyFor.id}
          projectName={historyFor.name}
          onClose={() => setHistoryFor(null)}
        />
      )}
      {toast && (
        <div
          role="alert"
          style={{
            position: 'fixed',
            top: 16,
            right: 16,
            maxWidth: 420,
            zIndex: 2000,
            background: '#fff7e6',
            border: '1px solid #f2d59c',
            color: '#8a5a00',
            padding: '12px 14px',
            borderRadius: 10,
            boxShadow: '0 10px 25px rgba(0,0,0,0.12)',
            whiteSpace: 'pre-wrap',
          }}
        >
          <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
            <div style={{ fontWeight: 600 }}>Внимание</div>
            <button
              type="button"
              onClick={() => setToast(null)}
              className="btn btn--ghost"
              style={{ padding: '2px 6px', marginLeft: 'auto' }}
            >
              ✕
            </button>
          </div>
          <div style={{ marginTop: 6 }}>{toast}</div>
        </div>
      )}
    </div>
  );
}

export default App;
