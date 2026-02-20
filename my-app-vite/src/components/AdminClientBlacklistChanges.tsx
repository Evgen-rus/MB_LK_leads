// Экран "События черного списка" для админа.
// Показывает необработанные события клиента по чёрному списку (добавления/удаления),
// и позволяет отметить их как обработанные (через /admin/changes/{id}/resolve).
import { useEffect, useMemo, useRef, useState } from 'react';
import { fetchAdminClientChanges, resolveAdminChange, type AdminChange, type AdminChangeStatus } from '../api';
import DateTimeCompact from './DateTimeCompact';

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

function extractBlacklistPhones(change: AdminChange): string[] {
  const snap = change.projectSnapshot;
  if (!snap || typeof snap !== 'object') return [];
  if (!('phones' in snap)) return [];
  const raw = (snap as { phones?: unknown }).phones;
  if (!Array.isArray(raw)) return [];
  return raw.map((x) => String(x)).filter(Boolean);
}

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
  const [phonesModalText, setPhonesModalText] = useState<string | null>(null);
  const phonesRef = useRef<HTMLTextAreaElement | null>(null);

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

  useEffect(() => {
    if (!phonesModalText) return;
    // Авто-выделение, чтобы админ мог сразу Ctrl+C
    const t = setTimeout(() => {
      try {
        phonesRef.current?.focus();
        phonesRef.current?.select();
      } catch {
        /* ignore */
      }
    }, 0);
    return () => clearTimeout(t);
  }, [phonesModalText]);

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
              <th style={{ width: 220 }}>Кто</th>
              <th>Описание</th>
              <th style={{ width: 260, textAlign: 'right' }}>Действия</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td className="muted" colSpan={5} style={{ padding: 16 }}>
                  Загрузка…
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td className="muted" colSpan={5} style={{ padding: 16 }}>
                  Список пуст
                </td>
              </tr>
            ) : (
              items.map((c) => (
                <tr key={c.id}>
                  <td className="muted" style={{ whiteSpace: 'nowrap' }}><DateTimeCompact value={c.createdAt} /></td>
                  <td className="name" style={{ whiteSpace: 'nowrap' }}>
                    {c.action === 'blacklist_add' ? 'Добавление' : c.action === 'blacklist_delete' ? 'Удаление' : c.action}
                  </td>
                  <td>
                    {c.actor ? (
                      <div>
                        <div className="name">{c.actor.login}</div>
                        <div className="sub" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                          <span>id: {c.actor.id}</span>
                          {c.actorMode === 'admin_impersonation' && (
                            <span className="badge badge--gray">через имперсонацию</span>
                          )}
                        </div>
                      </div>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td>{c.description}</td>
                  <td style={{ textAlign: 'right' }}>
                    <div style={{ display: 'inline-flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                      {c.action === 'blacklist_add' && (
                        <button
                          type="button"
                          className="btn btn--secondary"
                          onClick={() => {
                            const phones = extractBlacklistPhones(c);
                            setPhonesModalText(phones.join('\n'));
                          }}
                        >
                          Показать телефоны
                        </button>
                      )}
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

      {phonesModalText != null && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.4)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1100,
            padding: 16,
          }}
          onMouseDown={(e) => {
            // клик по фону закрывает
            if (e.target === e.currentTarget) setPhonesModalText(null);
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            className="modal-card"
            style={{
              background: '#fff',
              borderRadius: 8,
              width: '100%',
              maxWidth: 520,
              maxHeight: '90vh',
              overflow: 'hidden',
              boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            <div
              style={{
                padding: 16,
                borderBottom: '1px solid #eee',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 12,
              }}
            >
              <div style={{ fontSize: '1.05rem', fontWeight: 700 }}>Телефоны (ЧС)</div>
              <button className="icon-btn" aria-label="Закрыть" onClick={() => setPhonesModalText(null)}>✕</button>
            </div>
            <div style={{ padding: 16 }}>
              <textarea
                ref={phonesRef}
                readOnly
                value={phonesModalText}
                rows={16}
                style={{
                  width: '100%',
                  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
                  fontSize: 13,
                }}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default AdminClientBlacklistChanges;


