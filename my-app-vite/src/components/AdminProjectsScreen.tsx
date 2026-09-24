// Админский экран «Проекты»
// Задача: работать с проектами и изменениями ОДНОГО выбранного клиента.
// Важно: пользовательскую версию вкладки «Проекты» не трогаем, этот экран
// используется только для админов.
//
// Источники данных:
//   - список клиентов (админских пользователей) берём через fetchAdminUsers;
//   - проекты и изменения по клиенту отрисовывают уже готовые компоненты:
//       AdminClientProjects и AdminClientChanges.
//
// Если бэку когда‑нибудь понадобится более сложная фильтрация (по изменениям и т.п.),
// сюда можно будет добавить отдельные фильтры и прокинуть их дальше.

import { useEffect, useMemo, useState } from 'react';
import {
  fetchAdminUsers,
  fetchAdminClientChanges,
  runAdminOperatorBlockCheck,
  type UserInfo,
  type AdminChange,
  type AdminChangeStatus,
} from '../api';
import AdminClientProjects from './AdminClientProjects';
import AdminClientChanges from './AdminClientChanges';
import AdminClientBlacklistChanges from './AdminClientBlacklistChanges';
import DateRangeFilter from './DateRangeFilter';
import { formatProjectNameForDisplay } from '../utils/sourceCodeDisplay';

export type AdminProjectsFocus = 'projects' | 'changes' | 'blacklist-changes' | null;

export type AdminProjectsScreenProps = {
  managerRole: 'admin' | 'agent';
  // Опционально: предварительно выбранный клиент (например, при переходе из экрана «Клиенты»)
  initialClientId?: number | null;
  initialClientName?: string | null;
  // Куда сконцентрировать внимание после открытия: сразу на проектах или на изменениях
  initialFocus?: AdminProjectsFocus;
  onOpenLeads?: (params: {
    clientId: number;
    clientName: string;
    projectId: number;
    fromDate: string;
    toDate: string;
  }) => void;
  onOpenClientBlacklist?: (params: { clientId: number; clientName: string }) => void;
};

type ClientOption = {
  id: number;
  name: string;
};

type DateRange = {
  from: string;
  to: string;
};

function getTodayRange(): DateRange {
  const today = new Date().toISOString().slice(0, 10);
  return { from: today, to: today };
}

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

function AdminProjectsScreen({
  managerRole,
  initialClientId = null,
  initialClientName = null,
  initialFocus = 'projects',
  onOpenLeads,
  onOpenClientBlacklist,
}: AdminProjectsScreenProps) {
  const [clients, setClients] = useState<ClientOption[]>([]);
  const [loadingClients, setLoadingClients] = useState(false);
  const [clientsError, setClientsError] = useState<string | null>(null);
  const [operatorCheckRunning, setOperatorCheckRunning] = useState(false);
  const [projectChanges, setProjectChanges] = useState<Record<number, number>>({});
  const [projectCreates, setProjectCreates] = useState<Record<number, number>>({});
  const [clientPendingSummary, setClientPendingSummary] = useState<{ updates: number; creates: number; total: number }>({ updates: 0, creates: 0, total: 0 });
  const [clientBlacklistPendingSummary, setClientBlacklistPendingSummary] = useState<{ adds: number; deletes: number; total: number }>({ adds: 0, deletes: 0, total: 0 });

  const [selectedClientId, setSelectedClientId] = useState<number | null>(
    initialClientId ?? null,
  );
  const [selectedClientName, setSelectedClientName] = useState<string | null>(
    initialClientName ?? null,
  );

  const [focus, setFocus] = useState<AdminProjectsFocus>(initialFocus ?? 'projects');
  const [range, setRange] = useState<DateRange>(() => getTodayRange());

  useEffect(() => {
    if (initialClientId == null) return;
    setSelectedClientId(initialClientId);
    setSelectedClientName(initialClientName ?? null);
    setFocus(initialFocus ?? 'projects');
  }, [initialClientId, initialClientName, initialFocus]);

  // Загрузка списка клиентов для селекта
  useEffect(() => {
    (async () => {
      try {
        setLoadingClients(true);
        setClientsError(null);
        const users: UserInfo[] = await fetchAdminUsers();
        const options: ClientOption[] = users.map((u) => ({
          id: u.id,
          name: u.name || u.login,
        }));
        setClients(options);

        // Если нам передали initialClientId, но имени нет — попробуем взять его из списка
        if (initialClientId != null && !initialClientName) {
          const found = options.find((c) => c.id === initialClientId);
          if (found) {
            setSelectedClientName(found.name);
          }
        }
      } catch (err: unknown) {
        console.error(err);
        setClientsError(getErrorMessage(err, 'Не удалось загрузить список клиентов'));
      } finally {
        setLoadingClients(false);
      }
    })();
    // initial* не включаем в зависимости намеренно — они используются только при монтировании
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const hasSelectedClient = selectedClientId != null;

  const selectedClientLabel = useMemo(() => {
    if (!hasSelectedClient || selectedClientId == null) return '';
    if (selectedClientName) return `${selectedClientName} (id: ${selectedClientId})`;
    const found = clients.find((c) => c.id === selectedClientId);
    if (!found) return `id: ${selectedClientId}`;
    return `${found.name} (id: ${found.id})`;
  }, [hasSelectedClient, selectedClientId, selectedClientName, clients]);

  async function handleRunOperatorBlockCheck() {
    try {
      setOperatorCheckRunning(true);
      const result = await runAdminOperatorBlockCheck();
      const lines = [
        `Проверено B4-проектов: ${result.checked}.`,
        `Переведено в статус «Блокировка оператора»: ${result.blocked}.`,
        `Пропущено: ${result.skipped}.`,
      ];
      if (result.blockedProjects.length > 0) {
        const projectLines = result.blockedProjects
          .slice(0, 10)
          .map((project) => {
            const client = project.clientName ? `, клиент: ${project.clientName}` : '';
            return `- ${formatProjectNameForDisplay(project.name)} (id: ${project.id}${client})`;
          });
        lines.push('Проекты:');
        lines.push(projectLines.join('\n'));
        if (result.blockedProjects.length > 10) {
          lines.push(`... и ещё ${result.blockedProjects.length - 10}`);
        }
      }
      if (result.errors.length > 0) {
        lines.push(`Ошибок: ${result.errors.length}.`);
        lines.push(result.errors.slice(0, 3).join('\n'));
      }
      window.dispatchEvent(new CustomEvent('app-toast', { detail: lines.join('\n') }));
      window.dispatchEvent(new CustomEvent('projects-refresh'));
    } catch (err: unknown) {
      console.error(err);
      window.dispatchEvent(new CustomEvent('app-toast', { detail: getErrorMessage(err, 'Не удалось запустить проверку B4') }));
    } finally {
      setOperatorCheckRunning(false);
    }
  }

  // Загрузка изменений клиента для подсветки проектов с изменениями
  useEffect(() => {
    if (!selectedClientId) {
      setProjectChanges({});
      setProjectCreates({});
      setClientPendingSummary({ updates: 0, creates: 0, total: 0 });
      setClientBlacklistPendingSummary({ adds: 0, deletes: 0, total: 0 });
      return;
    }
    (async () => {
      try {
        // изменения (update/delete)
        const respChanges = await fetchAdminClientChanges(selectedClientId, { actions: ['update', 'delete'] });
        const mapChanges: Record<number, number> = {};
        let updatesCount = 0;
        respChanges.items.forEach((c: AdminChange) => {
          if (c.projectId == null) return;
          const status = (c.status as AdminChangeStatus | undefined) ?? 'pending';
          if (status === 'done') return;
          updatesCount += 1;
          mapChanges[c.projectId] = (mapChanges[c.projectId] ?? 0) + 1;
        });
        setProjectChanges(mapChanges);

        // создания
        const respCreates = await fetchAdminClientChanges(selectedClientId, { actions: ['create'] });
        const mapCreates: Record<number, number> = {};
        let createsCount = 0;
        respCreates.items.forEach((c: AdminChange) => {
          if (c.projectId == null) return;
          const status = (c.status as AdminChangeStatus | undefined) ?? 'pending';
          if (status === 'done') return;
          createsCount += 1;
          mapCreates[c.projectId] = (mapCreates[c.projectId] ?? 0) + 1;
        });
        setProjectCreates(mapCreates);

        setClientPendingSummary({
          updates: updatesCount,
          creates: createsCount,
          total: updatesCount + createsCount,
        });

        // события черного списка: отдельно считаем добавления и удаления
        const [respBlAdd, respBlDel] = await Promise.all([
          fetchAdminClientChanges(selectedClientId, { actions: ['blacklist_add'] }),
          fetchAdminClientChanges(selectedClientId, { actions: ['blacklist_delete'] }),
        ]);
        const adds = (respBlAdd?.items || []).filter((c) => ((c.status as AdminChangeStatus | undefined) ?? 'pending') !== 'done').length;
        const deletes = (respBlDel?.items || []).filter((c) => ((c.status as AdminChangeStatus | undefined) ?? 'pending') !== 'done').length;
        setClientBlacklistPendingSummary({ adds, deletes, total: adds + deletes });
      } catch (err: unknown) {
        console.error(err);
      }
    })();
  }, [selectedClientId]);

  return (
    <div className="admin-projects-screen">
      <div className="table-card">
        <div className="table-toolbar admin-projects-toolbar">
          <div className="filters admin-projects-filters">
            <select
              value={selectedClientId ?? ''}
              onChange={(e) => {
                const val = e.target.value ? Number(e.target.value) : null;
                setSelectedClientId(val);
                if (val == null) {
                  setSelectedClientName(null);
                } else {
                  const found = clients.find((c) => c.id === val);
                  setSelectedClientName(found ? found.name : null);
                }
              }}
            >
              <option value="">
                {loadingClients ? 'Загрузка клиентов…' : 'Выберите клиента'}
              </option>
              {clients.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} (id: {c.id})
                </option>
              ))}
            </select>
            <DateRangeFilter
              from={range.from}
              to={range.to}
              onChange={(r) => {
                setRange(r);
              }}
            />
          </div>
          <div className="actions">
            <button
              type="button"
              className="btn btn--secondary admin-projects-b4"
              onClick={handleRunOperatorBlockCheck}
              disabled={operatorCheckRunning}
              title="Проверить блокировки B4"
            >
              <span className="admin-projects-b4__full">{operatorCheckRunning ? 'Проверка B4…' : 'Проверить блокировки B4'}</span>
              <span className="admin-projects-b4__short">B4</span>
            </button>
            {clientsError && (
              <span className="sub" style={{ color: '#d00' }}>
                {clientsError}
              </span>
            )}
            {!clientsError && hasSelectedClient && (
              <span className="sub admin-projects-client">
                Выбран клиент: {selectedClientLabel}
                {(Object.keys(projectChanges).length > 0 || Object.keys(projectCreates).length > 0) && (
                  <span style={{ marginLeft: 8 }}>
                    · Есть необработанные события по проектам
                  </span>
                )}
              </span>
            )}
          </div>
        </div>

        {!hasSelectedClient && (
          <div
            style={{
              padding: 24,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <div style={{ fontWeight: 600 }}>Клиент не выбран</div>
            <div className="sub" style={{ textAlign: 'center', maxWidth: 480 }}>
              Выберите клиента сверху, чтобы увидеть его проекты и изменения.
            </div>
          </div>
        )}

        {hasSelectedClient && (
          <div className="admin-projects-tabs">
              <button
                type="button"
                className={focus === 'projects' ? 'btn btn--primary' : 'btn btn--secondary'}
                onClick={() => setFocus('projects')}
              >
                Проекты клиента
              </button>
            <button
              type="button"
              className={focus === 'changes' ? 'btn btn--primary' : 'btn btn--secondary'}
              onClick={() => setFocus('changes')}
            >
              Изменения клиента
              {(clientPendingSummary.updates > 0 || clientPendingSummary.creates > 0) && (
                <span className="sub" style={{ marginLeft: 8, display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                  {clientPendingSummary.updates > 0 && (
                    <span className="badge badge--orange" style={{ fontWeight: 500 }}>
                      Изм: {clientPendingSummary.updates}
                    </span>
                  )}
                  {clientPendingSummary.creates > 0 && (
                    <span className="badge badge--gray" style={{ fontWeight: 500 }}>
                      Созд: {clientPendingSummary.creates}
                    </span>
                  )}
                </span>
              )}
            </button>
            <button
              type="button"
              className={focus === 'blacklist-changes' ? 'btn btn--primary' : 'btn btn--secondary'}
              onClick={() => setFocus('blacklist-changes')}
            >
              События ЧС
              {(clientBlacklistPendingSummary.adds > 0 || clientBlacklistPendingSummary.deletes > 0) && (
                <span className="sub" style={{ marginLeft: 8, display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                  {clientBlacklistPendingSummary.adds > 0 && (
                    <span className="badge badge--orange" style={{ fontWeight: 500 }}>
                      ЧС+: {clientBlacklistPendingSummary.adds}
                    </span>
                  )}
                  {clientBlacklistPendingSummary.deletes > 0 && (
                    <span className="badge badge--gray" style={{ fontWeight: 500 }}>
                      ЧС−: {clientBlacklistPendingSummary.deletes}
                    </span>
                  )}
                </span>
              )}
            </button>
          </div>
        )}
      </div>

      {hasSelectedClient && selectedClientId != null && selectedClientName && (
        <>
          {/* Основная работа с проектами клиента */}
          {(!focus || focus === 'projects') && (
            <AdminClientProjects
              clientId={selectedClientId}
              clientName={selectedClientName}
              fromDate={range.from}
              toDate={range.to}
              projectChanges={projectChanges}
              projectCreates={projectCreates}
              managerRole={managerRole}
              onOpenLeads={onOpenLeads}
            />
          )}

          {/* Работа с изменениями клиента — теперь внутри вкладки «Проекты», а не на экране «Клиенты» */}
          {focus === 'changes' && (
            <AdminClientChanges
              clientId={selectedClientId}
              clientName={selectedClientName}
              onResolvedChange={({ change, processed = 1 }) => {
                const projectId = change.projectId ?? null;

                // Обновляем карты по проектам
                if (projectId != null) {
                  if (change.action === 'create') {
                    setProjectCreates((prev) => {
                      const prevCount = prev[projectId] ?? 0;
                      const next = Math.max(0, prevCount - processed);
                      if (next === 0) {
                        const rest = { ...prev };
                        delete rest[projectId];
                        return rest;
                      }
                      return { ...prev, [projectId]: next };
                    });
                  } else {
                    setProjectChanges((prev) => {
                      const prevCount = prev[projectId] ?? 0;
                      const next = Math.max(0, prevCount - processed);
                      if (next === 0) {
                        const rest = { ...prev };
                        delete rest[projectId];
                        return rest;
                      }
                      return { ...prev, [projectId]: next };
                    });
                  }
                }

                // Обновляем сводку по клиенту
                setClientPendingSummary((prev) => {
                  if (change.action === 'create') {
                    const creates = Math.max(0, (prev.creates ?? 0) - processed);
                    const total = Math.max(0, (prev.total ?? 0) - processed);
                    return { ...prev, creates, total };
                  }
                  const updates = Math.max(0, (prev.updates ?? 0) - processed);
                  const total = Math.max(0, (prev.total ?? 0) - processed);
                  return { ...prev, updates, total };
                });
              }}
            />
          )}

          {focus === 'blacklist-changes' && (
            <AdminClientBlacklistChanges
              clientId={selectedClientId}
              clientName={selectedClientName}
              onOpenBlacklist={() => onOpenClientBlacklist?.({ clientId: selectedClientId, clientName: selectedClientName })}
              onResolvedChange={({ change, processed = 1 }) => {
                // Обновляем сводку по ЧС
                setClientBlacklistPendingSummary((prev) => {
                  if (change.action === 'blacklist_add') {
                    const adds = Math.max(0, (prev.adds ?? 0) - processed);
                    const total = Math.max(0, (prev.total ?? 0) - processed);
                    return { ...prev, adds, total };
                  }
                  if (change.action === 'blacklist_delete') {
                    const deletes = Math.max(0, (prev.deletes ?? 0) - processed);
                    const total = Math.max(0, (prev.total ?? 0) - processed);
                    return { ...prev, deletes, total };
                  }
                  return prev;
                });
              }}
            />
          )}
        </>
      )}
    </div>
  );
}

export default AdminProjectsScreen;


