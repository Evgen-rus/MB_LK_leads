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
import { fetchAdminUsers, type UserInfo } from '../api';
import AdminClientProjects from './AdminClientProjects';
import AdminClientChanges from './AdminClientChanges';

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

function AdminProjectsScreen({
  initialClientId = null,
  initialClientName = null,
  initialFocus = 'projects',
}: AdminProjectsScreenProps) {
  const [clients, setClients] = useState<ClientOption[]>([]);
  const [loadingClients, setLoadingClients] = useState(false);
  const [clientsError, setClientsError] = useState<string | null>(null);

  const [selectedClientId, setSelectedClientId] = useState<number | null>(
    initialClientId ?? null,
  );
  const [selectedClientName, setSelectedClientName] = useState<string | null>(
    initialClientName ?? null,
  );

  const [focus, setFocus] = useState<AdminProjectsFocus>(initialFocus ?? 'projects');

  // Загрузка списка клиентов для селекта
  useEffect(() => {
    (async () => {
      try {
        setLoadingClients(true);
        setClientsError(null);
        const users: UserInfo[] = await fetchAdminUsers();
        const options: ClientOption[] = users.map((u) => ({
          id: u.id,
          name: u.login,
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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div className="table-card">
        <div className="table-toolbar">
          <div className="filters">
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
          </div>
          <div className="actions">
            {clientsError && (
              <span className="sub" style={{ color: '#d00' }}>
                {clientsError}
              </span>
            )}
            {!clientsError && hasSelectedClient && (
              <span className="sub">Выбран клиент: {selectedClientLabel}</span>
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
            </button>
          </div>
        )}
      </div>

      {hasSelectedClient && selectedClientId != null && selectedClientName && (
        <>
          {/* Основная работа с проектами клиента */}
          {(!focus || focus === 'projects') && (
            <AdminClientProjects clientId={selectedClientId} clientName={selectedClientName} />
          )}

          {/* Работа с изменениями клиента — теперь внутри вкладки «Проекты», а не на экране «Клиенты» */}
          {focus === 'changes' && (
            <AdminClientChanges
              clientId={selectedClientId}
              clientName={selectedClientName}
              // Здесь можно было бы обновлять какие‑то счётчики на уровне лэйаута,
              // но пока у нас нет единого стора — просто оставляем заглушку.
              onResolvedChange={() => {
                // Заглушка: при необходимости можно прокинуть событие наверх
                // и обновлять количество изменений по клиенту.
                // Например, через кастомный event:
                // window.dispatchEvent(new CustomEvent('admin-client-change-resolved', { detail: { clientId: selectedClientId } }));
              }}
            />
          )}
        </>
      )}
    </div>
  );
}

export default AdminProjectsScreen;


