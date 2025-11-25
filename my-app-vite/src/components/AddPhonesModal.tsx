import { useMemo, useState } from 'react';

type AddPhonesModalProps = {
  onClose: () => void;
  onSubmit: (phones: string[]) => void;
};

function normalizePhone(raw: string): string | null {
  const digits = raw.replace(/\D+/g, '');
  if (digits.length === 11) {
    const first = digits[0];
    const rest = digits.slice(1);
    const national = first === '8' ? '7' + rest : digits;
    if (national[0] === '7') return national;
  }
  // Попытка нормализовать 10-значный без кода страны
  if (digits.length === 10) {
    return '7' + digits;
  }
  return null;
}

function AddPhonesModal({ onClose, onSubmit }: AddPhonesModalProps) {
  const [text, setText] = useState('');

  const parsed = useMemo(() => {
    const unique = new Set<string>();
    const bad: string[] = [];
    text
      .split(/\r?\n/)
      .map(s => s.trim())
      .filter(Boolean)
      .forEach(line => {
        const norm = normalizePhone(line);
        if (norm) unique.add(norm);
        else bad.push(line);
      });
    return { ok: Array.from(unique), bad };
  }, [text]);

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
          maxWidth: 560,
          maxHeight: '90vh',
          overflowY: 'auto',
          boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
        }}
      >
        <div style={{ padding: 20, borderBottom: '1px solid #eee', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ fontSize: '1.125rem', fontWeight: 600 }}>Добавить телефоны</div>
          <button className="icon-btn" aria-label="Закрыть" onClick={onClose}>✕</button>
        </div>
        <div style={{ padding: 20 }}>
          <p style={{ marginTop: 0 }}>
            Добавьте номера телефонов, по которым вы не хотите получать сигналы. Формат: 79999999999.
          </p>
          <label className="label" style={{ display: 'block', fontSize: '0.75rem', color: '#666', marginBottom: 6 }}>Добавить номера телефонов</label>
          <textarea
            className=""
            rows={8}
            placeholder="Введите номера, каждый с новой строки"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <div style={{ marginTop: 8, fontSize: '0.75rem', color: '#666' }}>
            Корректных: {parsed.ok.length}
            {parsed.bad.length > 0 && (
              <span style={{ marginLeft: 12, color: '#b04949' }}>
                Некорректных: {parsed.bad.length}
              </span>
            )}
          </div>
        </div>
        <div style={{ padding: 20, borderTop: '1px solid #eee', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn btn--ghost" onClick={onClose}>Отменить</button>
          <button
            className="btn btn--primary"
            disabled={parsed.ok.length === 0}
            onClick={() => onSubmit(parsed.ok)}
          >
            Добавить
          </button>
        </div>
      </div>
    </div>
  );
}

export default AddPhonesModal;


