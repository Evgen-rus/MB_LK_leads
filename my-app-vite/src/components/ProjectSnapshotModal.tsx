type Props = {
  snapshot: Record<string, unknown>;
  projectName?: string;
  projectId?: number;
  createdAt?: string;
  action?: 'create' | 'update' | 'delete';
  onClose: () => void;
};

function ProjectSnapshotModal({ snapshot, projectName, projectId, createdAt, action = 'update', onClose }: Props) {
  const entries = Object.entries(snapshot || {});

  function copyJSON() {
    try {
      navigator.clipboard?.writeText(JSON.stringify(snapshot, null, 2));
    } catch (err: unknown) {
      console.error(err);
    }
  }

  return (
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
    >
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        style={{
          background: '#fff',
          borderRadius: 8,
          width: '100%',
          maxWidth: 900,
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
            flexWrap: 'wrap',
          }}
        >
          <div style={{ display: 'grid', gap: 4 }}>
            <div style={{ fontWeight: 600 }}>Карточка проекта ({action === 'create' ? 'Создание' : action === 'delete' ? 'Удаление' : 'Изменение'})</div>
            <div className="sub">
              {projectName ? `${projectName}` : 'Проект'}
              {projectId ? ` (id: ${projectId})` : ''}
            </div>
            {createdAt && <div className="sub">Создано: {createdAt}</div>}
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn" onClick={copyJSON} type="button">Копировать JSON</button>
            <button className="btn btn--secondary" onClick={onClose} type="button">Закрыть</button>
          </div>
        </div>

        <div style={{ padding: 16, overflowY: 'auto', flex: 1 }}>
          {entries.length === 0 && (
            <div className="muted">Нет данных карточки.</div>
          )}
          {entries.length > 0 && (
            <table className="table" style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={{ width: '30%' }}>Поле</th>
                  <th>Значение</th>
                  <th style={{ width: 120 }}>Копировать</th>
                </tr>
              </thead>
              <tbody>
                {entries.map(([key, value]) => (
                  <tr key={key}>
                    <td className="muted" style={{ whiteSpace: 'nowrap' }}>{key}</td>
                    <td>
                      <div style={{ maxWidth: 520, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                        {typeof value === 'object' ? JSON.stringify(value) : String(value ?? '')}
                      </div>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn btn--secondary"
                        onClick={() => {
                          try {
                            navigator.clipboard?.writeText(typeof value === 'object' ? JSON.stringify(value) : String(value ?? ''));
                          } catch (e) {
                            console.error(e);
                          }
                        }}
                      >
                        Копировать
                      </button>
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

export default ProjectSnapshotModal;

