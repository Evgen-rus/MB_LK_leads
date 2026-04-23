import { useEffect, useState } from 'react';
import type { ProjectHistoryItem } from '../api';
import { fetchProjectHistory } from '../api';
import DateTimeCompact from './DateTimeCompact';
import { formatProjectNameForDisplay, formatSourceTextForDisplay } from '../utils/sourceCodeDisplay';

type ProjectHistoryModalProps = {
  projectId: number;
  projectName?: string;
  onClose: () => void;
};

function ProjectHistoryModal({ projectId, projectName, onClose }: ProjectHistoryModalProps) {
  const [items, setItems] = useState<ProjectHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const getErrorMessage = (err: unknown, fallback: string): string => {
    if (err && typeof err === 'object' && 'message' in err) {
      const msg = (err as { message?: unknown }).message;
      if (typeof msg === 'string' && msg.trim()) return formatSourceTextForDisplay(msg);
    }
    return formatSourceTextForDisplay(fallback);
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const data = await fetchProjectHistory(projectId, 100);
        if (!cancelled) {
          setItems(data);
        }
      } catch (e: unknown) {
        if (!cancelled) {
          setError(getErrorMessage(e, 'Не удалось загрузить историю изменений'));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId]);

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
          maxWidth: 900,
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
            justifyContent: 'center',
            alignItems: 'center',
            position: 'relative',
          }}
        >
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: '1.125rem', fontWeight: 600 }}>История изменений проекта</div>
            {projectName && (
              <div style={{ fontSize: '0.875rem', color: '#666', marginTop: 4 }}>
                {formatProjectNameForDisplay(projectName)} (ID {projectId})
              </div>
            )}
          </div>
          <button
            type="button"
            className="btn"
            onClick={onClose}
            style={{ position: 'absolute', right: 20, top: 16 }}
          >
            Закрыть
          </button>
        </div>

        <div style={{ padding: 20 }}>
          {loading && <div>Загрузка истории…</div>}
          {error && !loading && (
            <div style={{ color: '#d00', fontSize: '0.875rem' }}>{error}</div>
          )}
          {!loading && !error && items.length === 0 && (
            <div style={{ fontSize: '0.9rem', color: '#666' }}>
              Изменений для этого проекта пока нет.
            </div>
          )}
          {!loading && !error && items.length > 0 && (
            <table className="table" style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left', padding: '8px 4px', fontSize: '0.75rem', color: '#666', whiteSpace: 'nowrap', width: 150 }}>Дата</th>
                  <th style={{ textAlign: 'left', padding: '8px 4px', fontSize: '0.75rem', color: '#666', whiteSpace: 'nowrap', width: 120 }}>Действие</th>
                  <th style={{ textAlign: 'left', padding: '8px 4px', fontSize: '0.75rem', color: '#666', whiteSpace: 'nowrap', width: 220 }}>Кто</th>
                  <th style={{ textAlign: 'left', padding: '8px 4px', fontSize: '0.75rem', color: '#666' }}>Описание</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id}>
                    <td style={{ padding: '6px 4px', fontSize: '0.875rem', color: '#555', whiteSpace: 'nowrap', width: 150 }}>
                      <DateTimeCompact value={item.createdAt} />
                    </td>
                    <td style={{ padding: '6px 4px', fontSize: '0.875rem', color: '#555', whiteSpace: 'nowrap', width: 120 }}>
                      {item.action === 'create' ? 'Создание' : item.action === 'update' ? 'Изменение' : 'Удаление'}
                    </td>
                    <td style={{ padding: '6px 4px', fontSize: '0.875rem', color: '#555', whiteSpace: 'nowrap', width: 220 }}>
                      {item.actor ? (
                        <>
                          {item.actor.login}
                          <span className="sub" style={{ marginLeft: 6 }}>
                            id: {item.actor.id}
                          </span>
                        </>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td style={{ padding: '6px 4px', fontSize: '0.875rem', color: '#111' }}>
                      {formatSourceTextForDisplay(item.description)}
                    </td>
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

export default ProjectHistoryModal;


