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
  fetchAdminClientChangesSummary,
  type UserInfo,
  type AdminChange,
  type AdminChangeStatus,
  type AdminClientChangesSummaryListOut,
} from '../api';
import AdminClientProjects from './AdminClientProjects';
import AdminClientChanges from './AdminClientChanges';
import DateRangeFilter from './DateRangeFilter';

export type AdminProjectsFocus = 'projects' | 'changes' | null;

export type AdminProjectsScreenProps = {
  // Опционально: предварительно выбранный клиент (например, при переходе из экрана «Клиенты»)
  initialClientId?: number | null;
  initialClientName?: string | null;
  // Куда сконцентрировать внимание после открытия: сразу на проектах или на изменениях
  initialFocus?: AdminProjectsFocus;
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

function AdminProjectsScreen({
  initialClientId = null,
  initialClientName = null,
  initialFocus = 'projects',
}: AdminProjectsScreenProps) {
  const [clients, setClients] = useState<ClientOption[]>([]);
  const [loadingClients, setLoadingClients] = useState(false);
  const [clientsError, setClientsError] = useState<string | null>(null);
  const [projectChanges, setProjectChanges] = useState<Record<number, number>>({});
  const [projectCreates, setProjectCreates] = useState<Record<number, number>>({});
  const [clientPendingSummary, setClientPendingSummary] = useState<{ updates: number; creates: number; total: number }>({ updates: 0, creates: 0, total: 0 });

  const [selectedClientId, setSelectedClientId] = useState<number | null>(
    initialClientId ?? null,
  );
  const [selectedClientName, setSelectedClientName] = useState<string | null>(
    initialClientName ?? null,
  );

  const [focus, setFocus] = useState<AdminProjectsFocus>(initialFocus ?? 'projects');
  const [range, setRange] = useState<DateRange>(() => getTodayRange());

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
      } catch (e: any) {
        console.error(e);
        setClientsError(e?.message || 'Не удалось загрузить список клиентов');
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

  // Загрузка изменений клиента для подсветки проектов с изменениями
  useEffect(() => {
    if (!selectedClientId) {
      setProjectChanges({});
      setProjectCreates({});
      setClientPendingSummary({ updates: 0, creates: 0, total: 0 });
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
      } catch (e: any) {
        console.error(e);
      }
    })();
  }, [selectedClientId]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div className="table-card">
        <div className="table-toolbar">
          <div className="filters" style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
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
            {clientsError && (
              <span className="sub" style={{ color: '#d00' }}>
                {clientsError}
              </span>
            )}
            {!clientsError && hasSelectedClient && (
              <span className="sub">
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
          <div
            style={{
              padding: 12,
              borderTop: '1px solid #eee',
              display: 'flex',
              gap: 8,
              flexWrap: 'wrap',
            }}
          >
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
                        const { [projectId]: _omit, ...rest } = prev;
                        return rest;
                      }
                      return { ...prev, [projectId]: next };
                    });
                  } else {
                    setProjectChanges((prev) => {
                      const prevCount = prev[projectId] ?? 0;
                      const next = Math.max(0, prevCount - processed);
                      if (next === 0) {
                        const { [projectId]: _omit, ...rest } = prev;
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
        </>
      )}
    </div>
  );
}

export default AdminProjectsScreen;


