// Экран "События черного списка" для админа.
// Показывает необработанные события клиента по чёрному списку (добавления/удаления),
// и позволяет отметить их как обработанные (через /admin/changes/{id}/resolve).
import { useEffect, useMemo, useState } from 'react';
import { fetchAdminClientChanges, resolveAdminChange, type AdminChange, type AdminChangeStatus } from '../api';

type ResolvedPayload = {
  change: AdminChange;
  processed?: number;
  batchId?: string | null;
};

type AdminClientBlacklistChangesProps = {
  clientId: number;
  clientName: string;
  onResolvedChange?: (payload: ResolvedPayload) => void;
  onOpenBlacklist?: () => void;
};

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

function AdminClientBlacklistChanges({
  clientId,
  clientName,
  onResolvedChange,
  onOpenBlacklist,
}: AdminClientBlacklistChangesProps) {
  const [allItems, setAllItems] = useState<AdminChange[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filterMode, setFilterMode] = useState<'adds' | 'deletes' | 'all'>('all');

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const resp = await fetchAdminClientChanges(clientId, { actions: ['blacklist_add', 'blacklist_delete'] });
        setAllItems(resp.items);
      } catch (err: unknown) {
        console.error(err);
        setError(getErrorMessage(err, 'Не удалось загрузить события чёрного списка'));
      } finally {
        setLoading(false);
      }
    })();
  }, [clientId]);

  const items = useMemo(() => {
    if (filterMode === 'adds') return allItems.filter((c) => c.action === 'blacklist_add');
    if (filterMode === 'deletes') return allItems.filter((c) => c.action === 'blacklist_delete');
    return allItems;
  }, [allItems, filterMode]);

  const counts = useMemo(() => {
    let adds = 0;
    let deletes = 0;
    allItems.forEach((c) => {
      const status = (c.status as AdminChangeStatus | undefined) ?? 'pending';
      if (status === 'done') return;
      if (c.action === 'blacklist_add') adds += 1;
      if (c.action === 'blacklist_delete') deletes += 1;
    });
    return { adds, deletes, total: adds + deletes };
  }, [allItems]);

  async function handleResolve(change: AdminChange) {
    try {
      const resp = await resolveAdminChange(change.id);
      const processedCount = typeof resp?.processed === 'number' ? resp.processed : 1;
      setAllItems((prev) => prev.filter((c) => c.id !== change.id));
      onResolvedChange?.({ change, processed: processedCount, batchId: resp?.batch ?? null });
    } catch (err: unknown) {
      console.error(err);
      setError(getErrorMessage(err, 'Не удалось отметить событие как обработанное'));
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
          gap: 12,
          flexWrap: 'wrap',
        }}
      >
        <div>
          <div style={{ fontWeight: 600 }}>События чёрного списка</div>
          <div className="sub">
            {clientName} (id: {clientId})
          </div>
        </div>

        <div className="sub">Необработанных всего: {counts.total}</div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button
            type="button"
            className={filterMode === 'all' ? 'btn btn--primary' : 'btn btn--secondary'}
            onClick={() => setFilterMode('all')}
          >
            Все
          </button>
          <button
            type="button"
            className={filterMode === 'adds' ? 'btn btn--primary' : 'btn btn--secondary'}
            onClick={() => setFilterMode('adds')}
          >
            Добавления
          </button>
          <button
            type="button"
            className={filterMode === 'deletes' ? 'btn btn--primary' : 'btn btn--secondary'}
            onClick={() => setFilterMode('deletes')}
          >
            Удаления
          </button>
          {onOpenBlacklist && (
            <button type="button" className="btn btn--secondary" onClick={onOpenBlacklist}>
              Открыть ЧС клиента
            </button>
          )}
        </div>
      </div>

      {error && (
        <div style={{ padding: 12, color: '#d00' }}>
          {error}
        </div>
      )}

      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 170 }}>Дата</th>
              <th style={{ width: 140 }}>Тип</th>
              <th>Описание</th>
              <th style={{ width: 260, textAlign: 'right' }}>Действия</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td className="muted" colSpan={4} style={{ padding: 16 }}>
                  Загрузка…
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td className="muted" colSpan={4} style={{ padding: 16 }}>
                  Список пуст
                </td>
              </tr>
            ) : (
              items.map((c) => (
                <tr key={c.id}>
                  <td className="muted" style={{ whiteSpace: 'nowrap' }}>{c.createdAt}</td>
                  <td className="name" style={{ whiteSpace: 'nowrap' }}>
                    {c.action === 'blacklist_add' ? 'Добавление' : c.action === 'blacklist_delete' ? 'Удаление' : c.action}
                  </td>
                  <td>{c.description}</td>
                  <td style={{ textAlign: 'right' }}>
                    <div style={{ display: 'inline-flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                      {onOpenBlacklist && (
                        <button type="button" className="btn btn--secondary" onClick={onOpenBlacklist}>
                          Открыть ЧС
                        </button>
                      )}
                      <button type="button" className="btn btn--primary" onClick={() => handleResolve(c)}>
                        Обработано
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default AdminClientBlacklistChanges;


