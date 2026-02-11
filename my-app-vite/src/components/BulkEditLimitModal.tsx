import { useState } from 'react';
import BulkEditModalFrame from './BulkEditModalFrame';

type BulkEditLimitModalProps = {
  selectedCount: number;
  submitting?: boolean;
  onClose: () => void;
  onSubmit: (limit: number) => void;
};

function BulkEditLimitModal({ selectedCount, submitting = false, onClose, onSubmit }: BulkEditLimitModalProps) {
  const [limitValue, setLimitValue] = useState<string>('50');

  const parsed = Number(limitValue);
  const isValid = Number.isFinite(parsed) && parsed >= 0;

  return (
    <BulkEditModalFrame
      selectedCount={selectedCount}
      onClose={onClose}
      onSubmit={() => onSubmit(Math.trunc(parsed))}
      submitDisabled={!isValid}
      submitting={submitting}
    >
      <label style={{ display: 'grid', gap: 6 }}>
        <span className="section-title">Лимит</span>
        <input
          type="number"
          min={0}
          value={limitValue}
          onChange={(e) => setLimitValue(e.target.value)}
          disabled={submitting}
        />
      </label>
      {!isValid && (
        <div className="sub" style={{ color: '#b42318' }}>
          Укажите корректное число (0 или больше).
        </div>
      )}
    </BulkEditModalFrame>
  );
}

export default BulkEditLimitModal;
