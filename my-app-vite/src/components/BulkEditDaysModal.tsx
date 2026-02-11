import { useState } from 'react';
import type { Day } from '../api';
import BulkEditModalFrame from './BulkEditModalFrame';

type BulkEditDaysModalProps = {
  selectedCount: number;
  submitting?: boolean;
  onClose: () => void;
  onSubmit: (days: Day[]) => void;
};

const WEEK_DAYS: Day[] = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

function BulkEditDaysModal({ selectedCount, submitting = false, onClose, onSubmit }: BulkEditDaysModalProps) {
  const [days, setDays] = useState<Day[]>(['Пн', 'Вт', 'Ср', 'Чт', 'Пт']);

  function toggleDay(day: Day) {
    setDays((prev) => (prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day]));
  }

  return (
    <BulkEditModalFrame
      selectedCount={selectedCount}
      onClose={onClose}
      onSubmit={() => onSubmit(days)}
      submitDisabled={days.length === 0}
      submitting={submitting}
    >
      <div className="section-title">Дни получения данных</div>
      <div className="hint">
        Будет выполнена полная замена дней у выбранных проектов.
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        {WEEK_DAYS.map((day) => (
          <label key={day}>
            <input
              type="checkbox"
              checked={days.includes(day)}
              onChange={() => toggleDay(day)}
              disabled={submitting}
            />{' '}
            {day}
          </label>
        ))}
      </div>
      {days.length === 0 && (
        <div className="sub" style={{ color: '#b42318' }}>
          Выберите хотя бы один день.
        </div>
      )}
    </BulkEditModalFrame>
  );
}

export default BulkEditDaysModal;
