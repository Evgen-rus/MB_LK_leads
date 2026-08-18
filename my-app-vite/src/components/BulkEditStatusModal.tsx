import { useState } from 'react';
import type { ProjectMutableStatus } from '../types/project';
import type { BulkProgress } from '../utils/projectBulkUpdate';
import BulkEditModalFrame from './BulkEditModalFrame';

type AllowedStatus = Exclude<ProjectMutableStatus, 'Удалён'>;

type BulkEditStatusModalProps = {
  selectedCount: number;
  submitting?: boolean;
  progress?: BulkProgress | null;
  onClose: () => void;
  onSubmit: (status: AllowedStatus) => void;
};

function BulkEditStatusModal({
  selectedCount,
  submitting = false,
  progress = null,
  onClose,
  onSubmit,
}: BulkEditStatusModalProps) {
  const [status, setStatus] = useState<AllowedStatus>('Активен');

  return (
    <BulkEditModalFrame
      selectedCount={selectedCount}
      onClose={onClose}
      onSubmit={() => onSubmit(status)}
      submitting={submitting}
      progress={progress}
    >
      <label style={{ display: 'grid', gap: 6 }}>
        <span className="section-title">Статус проекта</span>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value as AllowedStatus)}
          disabled={submitting}
        >
          <option value="Активен">Активен</option>
          <option value="На паузе">На паузе</option>
          <option value="Архив">Архив</option>
        </select>
      </label>
      <div className="hint">Статус «Удалён» в массовом редактировании недоступен.</div>
    </BulkEditModalFrame>
  );
}

export default BulkEditStatusModal;
