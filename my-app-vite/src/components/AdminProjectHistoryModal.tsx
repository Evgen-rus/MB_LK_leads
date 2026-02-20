import { useEffect, useState } from 'react';
import { fetchAdminProjectHistory, type AdminProjectHistoryItem } from '../api';
import DateRangeFilter from './DateRangeFilter';
import DateTimeCompact from './DateTimeCompact';

type Props = {
  projectId: number;
  projectName?: string;
  onClose: () => void;
};

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === 'object' && 'message' in err) {
    const msg = (err as { message?: unknown }).message;
    if (typeof msg === 'string' && msg.trim()) return msg;
  }
  return fallback;
}

function AdminProjectHistoryModal({ projectId, projectName, onClose }: Props) {
  const formatDate = (date: Date) => date.toISOString().slice(0, 10);
  const today = new Date();
  const monthAgo = new Date(today.getTime());
  monthAgo.setDate(today.getDate() - 30);

  const [fromDate, setFromDate] = useState<string>(formatDate(monthAgo));
  const [toDate, setToDate] = useState<string>(formatDate(today));
  const [status, setStatus] = useState<'all' | 'pending' | 'done'>('all');
  const [userId, setUserId] = useState<string>('');
  const [items, setItems] = useState<AdminProjectHistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const uidNum = userId ? Number(userId) : undefined;
        const resp = await fetchAdminProjectHistory(projectId, {
          fromDate,
          toDate,
          limit: 200,
          userId: Number.isFinite(uidNum) ? (uidNum as number) : undefined,
          status,
        });
        setItems(resp.items);
      } catch (err: unknown) {
        setError(getErrorMessage(err, 'Не удалось загрузить историю'));
      } finally {
        setLoading(false);
      }
    })();
  }, [projectId, fromDate, toDate, status, userId]);

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.4)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
        padding: 16,
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
          maxWidth: 1000,
          maxHeight: '90vh',
          overflowY: 'auto',
          boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
        }}
      >
        <div
          style={{
            padding: 20,
            borderBottom: '1px solid #eee',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <div>
            <div style={{ fontSize: '1.125rem', fontWeight: 600 }}>История изменений проекта</div>
            {projectName && (
              <div style={{ fontSize: '0.875rem', color: '#666', marginTop: 4 }}>
                {projectName} (ID {projectId})
              </div>
            )}
          </div>
          <button type="button" className="btn" onClick={onClose}>
            Закрыть
          </button>
        </div>

        <div style={{ padding: 16, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <DateRangeFilter
            from={fromDate}
            to={toDate}
            onChange={({ from, to }) => {
              setFromDate(from);
              setToDate(to);
            }}
          />
          <select value={status} onChange={(e) => setStatus(e.target.value as 'all' | 'pending' | 'done')}>
            <option value="all">Все статусы</option>
            <option value="pending">Не выполнено</option>
            <option value="done">Выполнено</option>
          </select>
          <input
            type="number"
            placeholder="ID пользователя"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            style={{ width: 140 }}
          />
        </div>

        <div style={{ padding: 16 }}>
          {loading && <div>Загрузка истории…</div>}
          {error && !loading && (
            <div style={{ color: '#d00', fontSize: '0.875rem' }}>{error}</div>
          )}
          {!loading && !error && items.length === 0 && (
            <div className="muted" style={{ padding: 8 }}>
              Изменений не найдено по выбранным фильтрам.
            </div>
          )}
          {!loading && !error && items.length > 0 && (
            <table className="table">
              <thead>
                <tr>
                  <th>Дата</th>
                  <th>Действие</th>
                  <th>Статус</th>
                  <th>Кто</th>
                  <th>Описание</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id}>
                    <td className="muted" style={{ whiteSpace: 'nowrap' }}><DateTimeCompact value={item.createdAt} /></td>
                    <td>{item.action === 'create' ? 'Создание' : item.action === 'update' ? 'Изменение' : 'Удаление'}</td>
                    <td>
                      <span className={item.status === 'done' ? 'badge badge--green' : 'badge badge--gray'}>
                        {item.status === 'done' ? 'Выполнено' : 'Не выполнено'}
                      </span>
                    </td>
                    <td>
                      {item.user ? (
                        <div>
                          <div className="name">{item.user.login}</div>
                          <div className="sub" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                            <span>id: {item.user.id}</span>
                            {item.actorMode === 'admin_impersonation' && (
                              <span className="badge badge--gray">через имперсонацию</span>
                            )}
                          </div>
                        </div>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>{item.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

export default AdminProjectHistoryModal;

