import type { ReactNode } from 'react';

type BulkEditModalFrameProps = {
  selectedCount: number;
  title?: string;
  children: ReactNode;
  onClose: () => void;
  onSubmit: () => void;
  submitLabel?: string;
  submitDisabled?: boolean;
  submitting?: boolean;
};

function BulkEditModalFrame({
  selectedCount,
  title = 'Массовое редактирование',
  children,
  onClose,
  onSubmit,
  submitLabel = 'Применить',
  submitDisabled = false,
  submitting = false,
}: BulkEditModalFrameProps) {
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0, 0, 0, 0.35)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1300,
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
        <div style={{ padding: 20, borderBottom: '1px solid #eee' }}>
          <div style={{ fontSize: '1.125rem', fontWeight: 600 }}>{title}</div>
          <div className="sub" style={{ marginTop: 4, color: '#666' }}>
            Проектов выбрано: {selectedCount}
          </div>
        </div>
        <div style={{ padding: 20, display: 'grid', gap: 12 }}>{children}</div>
        <div
          style={{
            position: 'sticky',
            bottom: 0,
            background: '#fff',
            padding: '12px 20px',
            borderTop: '1px solid #eee',
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 8,
          }}
        >
          <button type="button" className="btn" onClick={onClose} disabled={submitting}>
            Отмена
          </button>
          <button
            type="button"
            className="btn btn--primary"
            onClick={onSubmit}
            disabled={submitDisabled || submitting}
          >
            {submitting ? 'Сохранение...' : submitLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export default BulkEditModalFrame;
