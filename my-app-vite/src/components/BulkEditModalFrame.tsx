import type { ReactNode } from 'react';
import type { BulkProgress } from '../utils/projectBulkUpdate';

type BulkEditModalFrameProps = {
  selectedCount: number;
  title?: string;
  children: ReactNode;
  onClose: () => void;
  onSubmit: () => void;
  submitLabel?: string;
  submitDisabled?: boolean;
  submitting?: boolean;
  progress?: BulkProgress | null;
  progressUpdatedLabel?: string;
};

type BulkProgressBarProps = {
  progress: Pick<BulkProgress, 'total' | 'done'>;
  active?: boolean;
};

export function BulkProgressBar({ progress, active = true }: BulkProgressBarProps) {
  const progressPercent =
    progress.total > 0 ? Math.max(0, Math.min(100, Math.round((progress.done / progress.total) * 100))) : 0;

  return (
    <div
      className="bulk-progress-card__bar"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={progress.total}
      aria-valuenow={progress.done}
      aria-valuetext={`${progress.done} из ${progress.total}`}
    >
      <div
        className={`bulk-progress-card__bar-fill${active ? '' : ' bulk-progress-card__bar-fill--complete'}`}
        style={{ width: `${progressPercent}%` }}
      />
    </div>
  );
}

function BulkEditModalFrame({
  selectedCount,
  title = 'Массовое редактирование',
  children,
  onClose,
  onSubmit,
  submitLabel = 'Применить',
  submitDisabled = false,
  submitting = false,
  progress = null,
  progressUpdatedLabel = 'Обновлено',
}: BulkEditModalFrameProps) {
  const progressPercent =
    progress && progress.total > 0 ? Math.max(0, Math.min(100, Math.round((progress.done / progress.total) * 100))) : 0;

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
          {submitting && progress && (
            <div className="bulk-progress-card">
              <div className="bulk-progress-card__head">
                <div className="bulk-progress-card__status">
                  <span className="bulk-progress-card__dot" />
                  <span>Идёт обработка проектов</span>
                </div>
                <div className="bulk-progress-card__counter">
                  {progress.done}/{progress.total}
                </div>
              </div>
              <BulkProgressBar progress={progress} />
              <div className="bulk-progress-card__meta">
                <span>{progressPercent}% выполнено</span>
                <span>{progressUpdatedLabel}: {progress.updated}</span>
                <span>Пропущено: {progress.skipped}</span>
                <span>Ошибок: {progress.failed}</span>
              </div>
            </div>
          )}
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
