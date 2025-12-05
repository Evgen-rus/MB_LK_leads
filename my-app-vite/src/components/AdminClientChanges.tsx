// Экран "Изменения клиента" для админа.
// Показывает список необработанных изменений по проектам выбранного клиента.
import { useEffect, useMemo, useState } from 'react';
import { fetchAdminClientChanges, resolveAdminChange, type AdminChange, type AdminChangeStatus } from '../api';

type AdminClientChangesProps = {
  clientId: number;
  clientName: string;
  // onResolvedChange вызывается после успешной отметки изменения как выполненного.
  // Передаём всё изменение, чтобы родитель мог обновить счётчики по клиенту/проекту.
  onResolvedChange?: (change: AdminChange) => void;
};

type GroupedChanges = {
  projectId: number | null;
  projectName: string;
  items: AdminChange[];
};

function groupByProject(changes: AdminChange[]): GroupedChanges[] {
  const map = new Map<string, GroupedChanges>();
  changes.forEach((c) => {
    const pid = c.projectId ?? 0;
    const name = c.projectName || 'Без проекта';
    const key = `${pid}::${name}`;
    if (!map.has(key)) {
      map.set(key, { projectId: c.projectId ?? null, projectName: name, items: [] });
    }
    map.get(key)!.items.push(c);
  });
  return Array.from(map.values());
}

function AdminClientChanges({ clientId, clientName, onResolvedChange }: AdminClientChangesProps) {
  const [items, setItems] = useState<AdminChange[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [localStatus, setLocalStatus] = useState<Record<number, AdminChangeStatus>>({});

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const resp = await fetchAdminClientChanges(clientId);
        setItems(resp.items);
        // Инициализируем локальные статусы (если бэк вернёт статус — используем его)
        const map: Record<number, AdminChangeStatus> = {};
        resp.items.forEach((c) => {
          const s = (c.status as AdminChangeStatus | undefined) ?? 'created';
          map[c.id] = s;
        });
        setLocalStatus(map);
      } catch (e: any) {
        console.error(e);
        setError(e?.message || 'Не удалось загрузить изменения клиента');
      } finally {
        setLoading(false);
      }
    })();
  }, [clientId]);

  const grouped = useMemo(() => groupByProject(items), [items]);

  function getStatus(c: AdminChange): AdminChangeStatus {
    return localStatus[c.id] ?? ((c.status as AdminChangeStatus | undefined) ?? 'created');
  }

  function handleSetInProgress(id: number) {
    setLocalStatus((prev) => ({ ...prev, [id]: 'in_progress' }));
  }

  async function handleResolve(change: AdminChange) {
    const id = change.id;
    try {
      await resolveAdminChange(id);
      setItems((prev) => prev.filter((c) => c.id !== id));
      onResolvedChange?.(change);
    } catch (e) {
      console.error(e);
      alert('Не удалось отметить изменение как обработанное');
    }
  }

  return (
    <div className="table-card">
      <div
        style={{
          padding: 16,
          borderBottom: '1px solid #eee',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <div>
          <div style={{ fontWeight: 600 }}>Изменения клиента</div>
          <div className="sub">
            {clientName} (id: {clientId})
          </div>
        </div>
        <div className="sub">
          Всего необработанных изменений: {items.length}
        </div>
      </div>

      <div style={{ padding: 16 }}>
        {error && (
          <div style={{ color: '#d00', marginBottom: 12 }}>
            {error}
          </div>
        )}
        {loading && !error && (
          <div className="muted" style={{ padding: 8 }}>
            Загрузка изменений…
          </div>
        )}
        {!loading && !error && items.length === 0 && (
          <div className="muted" style={{ padding: 8 }}>
            Необработанных изменений нет.
          </div>
        )}

        {!loading && !error && grouped.map((g) => (
          <div
            key={`${g.projectId ?? 0}::${g.projectName}`}
            style={{
              border: '1px solid #eee',
              borderRadius: 8,
              padding: 12,
              marginBottom: 12,
            }}
          >
            <div style={{ marginBottom: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <div className="sub">Проект</div>
                <div>
                  {g.projectName}
                  {g.projectId != null && (
                    <span className="sub" style={{ marginLeft: 8 }}>
                      (id: {g.projectId})
                    </span>
                  )}
                </div>
              </div>
              <div className="sub">
                Изменений: {g.items.length}
              </div>
            </div>
            <table className="table" style={{ margin: 0 }}>
              <thead>
                <tr>
                  <th style={{ width: '24%' }}>Когда</th>
                  <th>Описание изменения</th>
                  <th style={{ width: '16%' }}>Статус</th>
                  <th style={{ width: 160 }}>Действия</th>
                </tr>
              </thead>
              <tbody>
                {g.items.map((c) => (
                  <tr key={c.id}>
                    <td className="muted" style={{ whiteSpace: 'nowrap' }}>{c.createdAt}</td>
                    <td>{c.description}</td>
                    <td>
                      {(() => {
                        const status = getStatus(c);
                        const label =
                          status === 'created'
                            ? 'Создано'
                            : status === 'in_progress'
                            ? 'В работе'
                            : 'Сделано';
                        const cls =
                          status === 'created'
                            ? 'badge badge--gray'
                            : status === 'in_progress'
                            ? 'badge badge--orange'
                            : 'badge badge--green';
                        return <span className={cls}>{label}</span>;
                      })()}
                    </td>
                    <td>
                      {getStatus(c) === 'created' && (
                        <button
                          type="button"
                          className="btn"
                          style={{ marginRight: 6 }}
                          onClick={() => handleSetInProgress(c.id)}
                        >
                          В работу
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn btn--secondary"
                        onClick={() => handleResolve(c)}
                      >
                        Отметить выполненным
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  );
}

export default AdminClientChanges;


