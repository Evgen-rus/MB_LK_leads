import type { Project } from '../types/project';
import type { BulkProgress } from '../utils/projectBulkUpdate';
import { formatProjectNameForDisplay } from '../utils/sourceCodeDisplay';
import BulkEditModalFrame from './BulkEditModalFrame';

type BulkDeleteProjectsModalProps = {
  selectedProjects: Project[];
  submitting?: boolean;
  progress?: BulkProgress | null;
  onClose: () => void;
  onSubmit: () => void;
};

function BulkDeleteProjectsModal({
  selectedProjects,
  submitting = false,
  progress = null,
  onClose,
  onSubmit,
}: BulkDeleteProjectsModalProps) {
  const previewProjects = selectedProjects.slice(0, 5);
  const hiddenCount = Math.max(0, selectedProjects.length - previewProjects.length);

  return (
    <BulkEditModalFrame
      title="Массовое удаление проектов"
      selectedCount={selectedProjects.length}
      onClose={onClose}
      onSubmit={onSubmit}
      submitLabel="Удалить проекты"
      submitting={submitting}
      progress={progress}
      progressUpdatedLabel="Удалено"
    >
      <div className="hint">
        Проекты будут удалены у провайдера, а в личном кабинете перейдут в статус «Удалён».
        Действие нельзя отменить из этого окна.
      </div>
      <div style={{ display: 'grid', gap: 6 }}>
        <div className="section-title">Будут удалены</div>
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {previewProjects.map((project) => (
            <li key={project.id}>
              {formatProjectNameForDisplay(project.name)} (id: {project.id})
            </li>
          ))}
        </ul>
        {hiddenCount > 0 && (
          <div className="sub">... и еще {hiddenCount} проект(ов)</div>
        )}
      </div>
    </BulkEditModalFrame>
  );
}

export default BulkDeleteProjectsModal;
