// Корневой лейаут приложения: левое меню и область контента с таблицей
import './App.css';
import Sidebar, { type ViewType } from './components/Sidebar';
import ProjectsTable from './components/ProjectsTable';
import LeadsTable from './components/LeadsTable';
import Integrations from './components/Integrations';
import Support from './components/Support';
import Blacklist from './components/Blacklist';
import Reports from './components/Reports';
import ClientBalance from './components/ClientBalance';
import ClientActivityHistory from './components/ClientActivityHistory';
import NotificationBell from './components/NotificationBell';
import AdminActivityBell from './components/AdminActivityBell';
import { lazy, Suspense, useState, useEffect } from 'react';
import CreateProjectModal from './components/CreateProjectModal';
import EditProjectModal from './components/EditProjectModal';
import ProjectHistoryModal from './components/ProjectHistoryModal';
import type { Project } from './types/project';
import type { AdminProjectsFocus } from './components/AdminProjectsScreen';
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
import { getRoleFromToken, isJwtValid } from './utils/jwt';
import { formatSourceTextForDisplay } from './utils/sourceCodeDisplay';

const STORAGE_VIEW_KEY = 'last_view';

const AdminLeadsTable = lazy(() => import('./components/AdminLeadsTable'));
const AdminBlacklist = lazy(() => import('./components/AdminBlacklist'));
const AdminReports = lazy(() => import('./components/AdminReports'));
const AdminClientsScreen = lazy(() => import('./components/AdminClientsScreen'));
const AdminAgentsScreen = lazy(() => import('./components/AdminAgentsScreen'));
const AdminDashboard = lazy(() => import('./components/AdminDashboard'));
const AdminProviderLeadsImport = lazy(() => import('./components/AdminProviderLeadsImport'));
const AdminProjectsScreen = lazy(() => import('./components/AdminProjectsScreen'));
const AdminBalance = lazy(() => import('./components/AdminBalance'));
const AdminActivityHistory = lazy(() => import('./components/AdminActivityHistory'));

function App() {
  type UserRole = 'admin' | 'agent' | 'client';
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
        saved === 'agents' ||
        saved === 'admin-dashboard' ||
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
  const [role, setRole] = useState<UserRole>('client');
  const isAdmin = role === 'admin';
  const isAgent = role === 'agent';
  const isManager = isAdmin || isAgent;
  // Состояние только для админов: какой клиент выбран во вкладке «Проекты»
  const [adminProjectsClientId, setAdminProjectsClientId] = useState<number | null>(null);
  const [adminProjectsClientName, setAdminProjectsClientName] = useState<string | null>(null);
  const [adminProjectsFocus, setAdminProjectsFocus] = useState<AdminProjectsFocus>('projects');
  // Состояние только для админов: фильтр клиента в «Черном списке»
  const [adminBlacklistClientId, setAdminBlacklistClientId] = useState<number | null>(null);
  // Состояние для баланса: выбранный клиент и какая модалка открыть
  const [adminBalanceClientId, setAdminBalanceClientId] = useState<number | null>(null);
  const [adminBalanceClientName, setAdminBalanceClientName] = useState<string | null>(null);
  const [adminBalanceModalType, setAdminBalanceModalType] = useState<'tariff' | null>(null);
  // Предзаполнение фильтров идентификаций при переходе из «Проектов»
  const [leadsPrefill, setLeadsPrefill] = useState<{ projectId?: number; from?: string; to?: string } | null>(null);
  // Предзаполнение идентификаций для админа (клиент + проект)
  const [adminLeadsPrefill, setAdminLeadsPrefill] = useState<{ clientId?: number; projectId?: number; from?: string; to?: string; unlinked?: boolean } | null>(null);
  // Клиентский баланс для шапки
  const [clientBalance, setClientBalance] = useState<{ remaining: number; debt: boolean } | null>(null);
  const [clientName, setClientName] = useState<string | null>(null);
  const [currentUserLogin, setCurrentUserLogin] = useState<string | null>(null);
  const [currentUserId, setCurrentUserId] = useState<number | null>(null);
  const [viaImpersonation, setViaImpersonation] = useState(false);
  const [impersonatorUserId, setImpersonatorUserId] = useState<number | null>(null);
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
        setToast(formatSourceTextForDisplay(detail));
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
          setRole('client');
          setCurrentUserLogin(null);
          setCurrentUserId(null);
          setViaImpersonation(false);
          setImpersonatorUserId(null);
          if (window.location.pathname !== '/login') {
            window.history.replaceState(null, '', '/login');
          }
        } else {
          const nextRole = getRoleFromToken(token) || 'client';
          setNeedLogin(false);
          setRole(nextRole);
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
    if (!isAdmin && (view === 'agents' || view === 'admin-dashboard')) {
      setView(isManager ? 'admin-clients' : 'projects');
      try {
        localStorage.setItem(STORAGE_VIEW_KEY, isManager ? 'admin-clients' : 'projects');
      } catch {
        /* ignore */
      }
      return;
    }
    if (!isManager && (view === 'admin-clients' || view === 'admin-provider-import')) {
      setView('projects');
      try {
        localStorage.setItem(STORAGE_VIEW_KEY, 'projects');
      } catch {
        /* ignore */
      }
      return;
    }
    // если вдруг сохранённая вкладка невалидная, откатываем
    if (
      view !== 'admin-dashboard' &&
      view !== 'agents' &&
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
  }, [authChecked, needLogin, isAdmin, isAgent, isManager, view]);

  // Загрузка данных после подтверждённой авторизации
  useEffect(() => {
    if (!authChecked || needLogin) return;
    if (isManager) return;
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
  }, [authChecked, needLogin, isManager]);

  // Подтягиваем баланс клиента для шапки (только для клиентской роли)
  useEffect(() => {
    if (!authChecked || needLogin || isManager) return;
    (async () => {
      try {
        const data = await fetchClientBalanceSummary();
        setClientBalance({ remaining: data.remaining, debt: data.debt });
      } catch (e) {
        console.error(e);
      }
    })();
  }, [authChecked, needLogin, isManager]);

  // Подтягиваем профиль текущего пользователя для шапки.
  useEffect(() => {
    if (!authChecked || needLogin || isAdmin) {
      setClientName(null);
      setCurrentUserLogin(null);
      setCurrentUserId(null);
      setViaImpersonation(false);
      setImpersonatorUserId(null);
      setProjectsMutationLocked(false);
      setProjectsMutationLockReason(null);
      setUniqueProjectNamesEnabled(false);
      return;
    }
    (async () => {
      try {
        const me = await fetchMe();
        setClientName(me.name || me.login || null);
        setCurrentUserLogin(me.login);
        setCurrentUserId(me.id);
        setViaImpersonation(Boolean(me.viaImpersonation));
        setImpersonatorUserId(me.impersonatorUserId ?? null);
        setProjectsMutationLocked(Boolean(me.projectsMutationLocked));
        setProjectsMutationLockReason(me.projectsMutationLockReason || null);
        setUniqueProjectNamesEnabled(Boolean(me.uniqueProjectNamesEnabled));
      } catch (e) {
        console.error(e);
        setClientName(null);
        setCurrentUserLogin(null);
        setCurrentUserId(null);
        setViaImpersonation(false);
        setImpersonatorUserId(null);
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
      const nextRole = getRoleFromToken(token) || 'client';
      setRole(nextRole);

      // Требование: дефолтная вкладка выставляется ТОЛЬКО после ввода логина/пароля.
      // При обычном обновлении страницы остаёмся на last_view.
      const nextView: ViewType = nextRole === 'client' ? 'leads' : nextRole === 'admin' ? 'admin-dashboard' : 'admin-clients';
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
          role={role}
        />
        <main className="main">
          <div className="page-title page-title--main">
            <div className="page-title__left">
              <span className="page-title__title">
                {view === 'agents'
                  ? 'Агенты'
                  : view === 'admin-dashboard'
                  ? 'Дашборд'
                  : view === 'admin-clients'
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
              {!isManager && clientName && (
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
              {isAgent && clientName && (
                <span
                  className="sub"
                  style={{
                    color: '#4b4570',
                    maxWidth: 420,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    padding: '4px 10px',
                    borderRadius: 999,
                    border: '1px solid #d9d3ff',
                    background: '#f4f1ff',
                  }}
                  title={`ЛК агента: ${clientName}${currentUserLogin ? ` (${currentUserLogin})` : ''}${currentUserId != null ? `, id: ${currentUserId}` : ''}`}
                >
                  {`ЛК агента: ${clientName}${currentUserLogin ? ` (${currentUserLogin})` : ''}${currentUserId != null ? `, id: ${currentUserId}` : ''}`}
                </span>
              )}
            </div>
          <div className="page-title__right">
            {!isManager && clientBalance && (
              <div className="page-title__balance" style={{ color: clientBalance.debt ? '#d23' : '#111' }}>
                <div className="page-title__balance-value">
                  <span className="page-title__balance-label-full">Текущий остаток:</span>
                  <span className="page-title__balance-label-short">Остаток:</span>{' '}
                  {clientBalance.remaining}
                </div>
                {clientBalance.debt && <div className="sub" style={{ color: '#d23' }}>Долг</div>}
              </div>
            )}
            {isManager ? (
              <AdminActivityBell
                onOpenFullHistory={() => {
                  setView('activity');
                  try {
                    localStorage.setItem(STORAGE_VIEW_KEY, 'activity');
                  } catch {
                    /* ignore */
                  }
                }}
              />
            ) : (
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
              setCurrentUserLogin(null);
              setCurrentUserId(null);
              setViaImpersonation(false);
              setImpersonatorUserId(null);
              setRows([]);
              setUniqueProjectNamesEnabled(false);
              setRole('client');
              setNeedLogin(true);
              if (window.location.pathname !== '/login') {
                window.history.replaceState(null, '', '/login');
              }
            }}>Выйти</button>
          </div>
          </div>
          {isAgent && viaImpersonation && (
            <div
              style={{
                marginTop: -4,
                marginBottom: 12,
                padding: '10px 12px',
                borderRadius: 12,
                border: '1px solid #f0cf8a',
                background: '#fff7e6',
                color: '#7a5300',
                display: 'flex',
                gap: 8,
                alignItems: 'center',
                flexWrap: 'wrap',
              }}
            >
              <strong>Служебный вход от имени агента</strong>
              <span>{clientName || currentUserLogin || 'Агент'}</span>
              {currentUserId != null && <span className="sub">id: {currentUserId}</span>}
              {impersonatorUserId != null && <span className="sub">администратор id: {impersonatorUserId}</span>}
            </div>
          )}
          <Suspense fallback={<div className="table-card" style={{ padding: 16 }}>Загрузка…</div>}>
            {view === 'admin-dashboard' && isAdmin ? (
              <AdminDashboard
                onOpenClient={(clientId) => {
                  setAdminProjectsClientId(null);
                  setAdminProjectsClientName(null);
                  setAdminBalanceClientId(null);
                  setAdminBalanceClientName(null);
                  setView('admin-clients');
                  try {
                    localStorage.setItem(STORAGE_VIEW_KEY, 'admin-clients');
                    localStorage.setItem('admin_clients_focus_id', String(clientId));
                  } catch {
                    /* ignore */
                  }
                }}
                onOpenProject={(clientId, clientName) => {
                  setAdminProjectsClientId(clientId);
                  setAdminProjectsClientName(clientName);
                  setAdminProjectsFocus('projects');
                  setView('projects');
                  try {
                    localStorage.setItem(STORAGE_VIEW_KEY, 'projects');
                  } catch {
                    /* ignore */
                  }
                }}
                onOpenLeads={(fromDate, toDate) => {
                  setAdminLeadsPrefill({ from: fromDate, to: toDate, unlinked: true });
                  setView('leads');
                  try {
                    localStorage.setItem(STORAGE_VIEW_KEY, 'leads');
                  } catch {
                    /* ignore */
                  }
                }}
                onOpenActivity={() => {
                  setView('activity');
                  try {
                    localStorage.setItem(STORAGE_VIEW_KEY, 'activity');
                  } catch {
                    /* ignore */
                  }
                }}
              />
            ) : view === 'agents' && isAdmin ? (
              <AdminAgentsScreen
                onOpenClientProjects={(clientId, clientName) => {
                  setAdminProjectsClientId(clientId);
                  setAdminProjectsClientName(clientName);
                  setAdminProjectsFocus('projects');
                  setView('projects');
                }}
                onOpenClientChanges={(clientId, clientName) => {
                  setAdminProjectsClientId(clientId);
                  setAdminProjectsClientName(clientName);
                  setAdminProjectsFocus('changes');
                  setView('projects');
                }}
                onOpenClientBlacklistChanges={(clientId, clientName) => {
                  setAdminProjectsClientId(clientId);
                  setAdminProjectsClientName(clientName);
                  setAdminProjectsFocus('blacklist-changes');
                  setView('projects');
                }}
                onOpenClientBalance={(clientId, clientName, action) => {
                  setAdminBalanceClientId(clientId);
                  setAdminBalanceClientName(clientName);
                  setAdminBalanceModalType(action);
                  setView('balance');
                }}
              />
            ) : view === 'admin-clients' && isManager ? (
              <AdminClientsScreen
                managerRole={role}
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
                onOpenClientBalance={(clientId, clientName, action) => {
                  setAdminBalanceClientId(clientId);
                  setAdminBalanceClientName(clientName);
                  setAdminBalanceModalType(action);
                  setView('balance');
                }}
              />
            ) : view === 'admin-provider-import' && isAdmin ? (
              <AdminProviderLeadsImport />
            ) : view === 'projects' ? (
              isManager ? (
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
              isManager ? <AdminLeadsTable initialFilter={adminLeadsPrefill ?? undefined} /> : <LeadsTable projects={rows} initialFilter={leadsPrefill ?? undefined} />
            ) : view === 'reports' ? (
              isManager ? <AdminReports managerRole={isAdmin ? 'admin' : 'agent'} /> : <Reports />
          ) : view === 'activity' ? (
            isManager ? <AdminActivityHistory /> : <ClientActivityHistory />
          ) : view === 'balance' ? (
            isManager ? (
              <AdminBalance
                managerRole={role}
                initialClientId={adminBalanceClientId ?? undefined}
                initialClientName={adminBalanceClientName ?? undefined}
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
              isManager ? <AdminBlacklist initialUserId={adminBlacklistClientId ?? undefined} /> : <Blacklist />
            )}
          </Suspense>
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
                setToast(formatSourceTextForDisplay(result.warning));
              }
              return null;
            } catch (e) {
              console.error(e);
              if (e instanceof Error && e.message) {
                return formatSourceTextForDisplay(e.message);
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
              setToast(formatSourceTextForDisplay(result.warning));
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
