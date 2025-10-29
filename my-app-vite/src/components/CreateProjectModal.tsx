import { useEffect, useRef, useState } from 'react';

type CreateProjectModalProps = {
  onClose: () => void;
  onSubmit?: (payload: {
    name: string;
    tag: string;
    type: string;
    dataLimit: number;
    status: 'Активен' | 'На паузе';
  }) => void;
};

function CreateProjectModal({ onClose, onSubmit }: CreateProjectModalProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null);

  const [name, setName] = useState('');
  const [tag, setTag] = useState('');
  const [type, setType] = useState('Звонки');
  const [dataLimit, setDataLimit] = useState<number>(100);
  const [status, setStatus] = useState<'Активен' | 'На паузе'>('Активен');

  useEffect(() => {
    setTag(name);
  }, [name]);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  function handleBackdropClick(e: React.MouseEvent<HTMLDivElement>) {
    if (e.target === e.currentTarget) onClose();
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    const payload = {
      name: name.trim(),
      tag: tag.trim() || name.trim(),
      type,
      dataLimit: Number.isFinite(dataLimit) ? dataLimit : 0,
      status,
    };
    if (onSubmit) {
      onSubmit(payload);
    }
    onClose();
  }

  return (
    <div
      onClick={handleBackdropClick}
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
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        style={{
          background: '#fff',
          borderRadius: 8,
          width: '100%',
          maxWidth: 560,
          boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
        }}
      >
        <div style={{ padding: 20, borderBottom: '1px solid #eee' }}>
          <div style={{ fontSize: 18, fontWeight: 600 }}>Создать проект</div>
        </div>
        <form onSubmit={handleSubmit} style={{ padding: 20 }}>
          <div style={{ display: 'grid', gap: 12 }}>
            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ fontSize: 12, color: '#666' }}>Название</span>
              <input
                autoFocus
                type="text"
                placeholder="Например, [LR172] тест6"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>

            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ fontSize: 12, color: '#666' }}>Тег</span>
              <input
                type="text"
                placeholder="По умолчанию как название"
                value={tag}
                onChange={(e) => setTag(e.target.value)}
              />
            </label>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ fontSize: 12, color: '#666' }}>Тип</span>
                <select value={type} onChange={(e) => setType(e.target.value)}>
                  <option value="Звонки">Звонки</option>
                  <option value="С кодом">С кодом</option>
                </select>
              </label>

              <label style={{ display: 'grid', gap: 6 }}>
                <span style={{ fontSize: 12, color: '#666' }}>Лимит</span>
                <input
                  type="number"
                  min={0}
                  value={dataLimit}
                  onChange={(e) => setDataLimit(Number(e.target.value))}
                />
              </label>
            </div>

            <label style={{ display: 'grid', gap: 6 }}>
              <span style={{ fontSize: 12, color: '#666' }}>Статус</span>
              <select value={status} onChange={(e) => setStatus(e.target.value as 'Активен' | 'На паузе')}>
                <option value="Активен">Активен</option>
                <option value="На паузе">На паузе</option>
              </select>
            </label>
          </div>

          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 20 }}>
            <button type="button" className="btn" onClick={onClose}>Отмена</button>
            <button type="submit" className="btn btn--primary">Создать</button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default CreateProjectModal;


